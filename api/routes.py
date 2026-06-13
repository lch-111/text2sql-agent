"""
==============================================================================
API 路由 — 所有后端接口
==============================================================================
"""

import json
import os
import asyncio
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import StreamingResponse

from pydantic import BaseModel
from api.schemas import ChatRequest, DashboardControlRequest, DbConnectionRequest
from api.dependencies import get_agent, get_db, get_cache
from api.streaming import stream_chat, _sse
from core.database import DatabaseManager

logger = logging.getLogger("api")
router = APIRouter(prefix="/api")


# ============================================================================
# 聊天
# ============================================================================

@router.post("/chat")
async def chat(body: ChatRequest, agent=Depends(get_agent)):
    """非流式聊天（一次性返回完整结果）"""
    try:
        result = agent.run(body.question)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest):
    """
    SSE 流式聊天。

    注意事项（ERR_INCOMPLETE_CHUNKED_ENCODING 防护）：
    1. 禁用所有缓存头，防止反向代理缓冲响应体
    2. X-Accel-Buffering: no — 禁用 Nginx 缓冲
    3. 后端每步 yield 后发 flush 信号 + await sleep(0.05)
    4. 异常捕获保证 always send done event
    """
    return StreamingResponse(
        stream_chat(body.question, body.history),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/chat/explain-chart")
async def explain_chart(body: ChatRequest):
    """
    使用 LLM 解读图表数据和 SQL，返回自然语言分析。
    """
    try:
        from core.llm_client import BaseLLMClient
        from core.config import CONFIG

        question = body.question  # 这里复用 ChatRequest，question 字段承载图表标题/描述
        history = body.history or []
        # history 中包含上一轮的 { sql, result, columns }
        chart_context = ""
        for msg in history:
            if msg.get("sql"):
                chart_context += f"SQL: {msg['sql']}\n"
            if msg.get("result"):
                import json
                chart_context += f"结果: {json.dumps(msg['result'][:10], ensure_ascii=False)}\n"

        if not chart_context:
            return {"analysis": "暂无图表数据可分析"}

        client = BaseLLMClient(
            model=CONFIG.llm.generator_model,
            name="explain-chart",
        )
        prompt = (
            f"你是一名数据分析师，请解读以下查询结果，给出自然语言分析。\n"
            f"分析要点：数据整体趋势、异常点、关键结论、业务建议。\n\n"
            f"查询标题：{question}\n\n"
            f"{chart_context}\n"
            f"请用中文输出分析报告，200 字以内。"
        )
        analysis = client.generate(
            prompt,
            system_prompt="你是一名资深数据分析师，擅长解读数据图表。",
        )
        return {"analysis": analysis}
    except Exception as e:
        logger.warning(f"[explain-chart] 分析失败: {e}")
        return {"analysis": "图表分析暂不可用"}


@router.post("/chart/recommend")
async def chart_recommend(body: ChatRequest):
    """
    使用 LLM 根据查询结果列名和样本数据，推荐图表配置（类型、轴、堆叠等）。

    请求格式：{ "question": "列名1,列名2,...", "history": [ { "rows": [...], "columns": [...] } ] }
    返回：{ "chartType": "bar", "xAxis": "col1", "yAxis": "col2", "seriesField": "", "stacked": false }
    LLM 失败时降级为默认推荐（柱状图 + 首列横轴 + 次列纵轴）。
    """
    from core.llm_client import BaseLLMClient
    from core.config import CONFIG

    # 从 history 中取出列名和样本数据
    history = body.history or []
    sample = {}
    for msg in history:
        if msg.get("columns") and msg.get("rows"):
            sample = msg
            break
    columns = sample.get("columns", [])
    rows = sample.get("rows", [])
    if not columns or not rows:
        return {"chartType": "bar", "xAxis": columns[0] if columns else "", "yAxis": columns[1] if len(columns) > 1 else columns[0], "seriesField": "", "stacked": False}

    try:
        client = BaseLLMClient(
            model=CONFIG.llm.generator_model,
            name="chart-recommend",
        )
        rows_str = "\n".join(str(r) for r in rows[:3])
        prompt = (
            "根据列名和样本数据，推荐 ECharts 图表类型及轴配置。\n"
            f"列名：{json.dumps(columns)}\n"
            f"样本：{rows_str}\n"
            "返回 JSON："
            '{"chartType":"bar","xAxis":"列名","yAxis":"列名","seriesField":"","stacked":false}'
            "\n仅返回 JSON，不要其他内容。chartType 可选：bar, line, pie, scatter, funnel, radar"
        )
        result = client.generate_json(
            prompt,
            system_prompt="你是一名资深数据可视化专家。",
        )
        if result and isinstance(result, dict) and result.get("chartType"):
            return {
                "chartType": result.get("chartType", "bar"),
                "xAxis": result.get("xAxis", columns[0]) if result.get("xAxis") in columns else columns[0],
                "yAxis": result.get("yAxis", columns[1] if len(columns) > 1 else columns[0]) if result.get("yAxis") in columns else (columns[1] if len(columns) > 1 else columns[0]),
                "seriesField": result.get("seriesField", "") if result.get("seriesField") in columns else "",
                "stacked": result.get("stacked", False),
            }
    except Exception as e:
        logger.warning(f"[chart-recommend] LLM 调用失败: {e}")

    # 降级：默认柱状图 + 首列横轴 + 第二列纵轴
    return {
        "chartType": "bar",
        "xAxis": columns[0] if columns else "",
        "yAxis": columns[1] if len(columns) > 1 else (columns[0] if columns else ""),
        "seriesField": "",
        "stacked": False,
    }


# ============================================================================
# 数据大屏 — KPI
# ============================================================================

@router.get("/kpi")
async def get_kpi(db=Depends(get_db)):
    """KPI 指标卡数据（自动适配当前数据库表结构）"""
    tables = db.get_table_names()
    tbl_set = set(tables)

    total_users = total_orders = total_products = total_revenue = 0

    try:
        if "users" in tbl_set:
            total_users = int(db.query("SELECT COUNT(*) as cnt FROM users").iloc[0]["cnt"])
    except Exception:
        pass

    try:
        if "orders" in tbl_set:
            total_orders = int(db.query("SELECT COUNT(*) as cnt FROM orders").iloc[0]["cnt"])
    except Exception:
        pass

    try:
        if "products" in tbl_set:
            total_products = int(db.query("SELECT COUNT(*) as cnt FROM products").iloc[0]["cnt"])
    except Exception:
        pass

    try:
        if "orders" in tbl_set:
            total_revenue = float(db.query(
                "SELECT COALESCE(SUM(total_amount), 0) as total FROM orders WHERE status = '已完成'"
            ).iloc[0]["total"])
    except Exception:
        pass

    return {
        "total_users": total_users,
        "total_orders": total_orders,
        "total_products": total_products,
        "total_revenue": total_revenue,
    }


@router.get("/dashboard/sales-trend")
async def get_sales_trend(view_mode: str = "daily", db=Depends(get_db)):
    """销售趋势数据（自动适配 MySQL / PostgreSQL / SQLite）"""
    tables = db.get_table_names()
    if "orders" not in tables:
        return {"periods": [], "sales": [], "orders": []}

    # 根据数据库类型选择兼容的日期格式化函数
    db_type = db.active_db_type
    try:
        if view_mode == "monthly":
            if db_type == "mysql":
                sql = "SELECT DATE_FORMAT(order_date, '%Y-%m') AS period, SUM(total_amount) AS sales, COUNT(*) AS orders FROM orders WHERE status = '已完成' GROUP BY period ORDER BY period"
            elif db_type == "postgres":
                sql = "SELECT TO_CHAR(order_date, 'YYYY-MM') AS period, SUM(total_amount) AS sales, COUNT(*) AS orders FROM orders WHERE status = '已完成' GROUP BY period ORDER BY period"
            else:
                sql = "SELECT strftime('%Y-%m', order_date) AS period, SUM(total_amount) AS sales, COUNT(*) AS orders FROM orders WHERE status = '已完成' GROUP BY period ORDER BY period"
        else:
            if db_type == "mysql":
                sql = "SELECT DATE(order_date) AS period, SUM(total_amount) AS sales, COUNT(*) AS orders FROM orders WHERE status = '已完成' GROUP BY period ORDER BY period"
            elif db_type == "postgres":
                sql = "SELECT order_date::date AS period, SUM(total_amount) AS sales, COUNT(*) AS orders FROM orders WHERE status = '已完成' GROUP BY period ORDER BY period"
            else:
                sql = "SELECT order_date AS period, SUM(total_amount) AS sales, COUNT(*) AS orders FROM orders WHERE status = '已完成' GROUP BY order_date ORDER BY order_date"

        df = db.query(sql)
        return {
            "periods": df["period"].tolist() if not df.empty else [],
            "sales": [float(v) for v in df["sales"]] if not df.empty else [],
            "orders": [int(v) for v in df["orders"]] if not df.empty else [],
        }
    except Exception as e:
        return {"periods": [], "sales": [], "orders": [], "error": str(e)}


@router.get("/dashboard/category-analysis")
async def get_category_analysis(db=Depends(get_db)):
    """商品类别分析数据（自动适配当前数据库表结构）"""
    tables = db.get_table_names()
    if "orders" not in tables or "products" not in tables:
        return {"categories": [], "order_counts": [], "total_qty": [], "total_sales": []}

    db_type = db.active_db_type
    try:
        if db_type == "mysql":
            sql = ("SELECT p.category, COUNT(DISTINCT o.order_id) AS order_count, "
                   "SUM(o.quantity) AS total_qty, ROUND(SUM(o.total_amount), 2) AS total_sales "
                   "FROM orders o JOIN products p ON o.product_id = p.product_id "
                   "WHERE o.status = '已完成' GROUP BY p.category ORDER BY total_sales DESC")
        else:
            sql = ("SELECT p.category, COUNT(DISTINCT o.order_id) AS order_count, "
                   "SUM(o.quantity) AS total_qty, ROUND(SUM(o.total_amount), 2) AS total_sales "
                   "FROM orders o JOIN products p ON o.product_id = p.product_id "
                   "WHERE o.status = '已完成' GROUP BY p.category ORDER BY total_sales DESC")

        df = db.query(sql)
        return {
            "categories": df["category"].tolist() if not df.empty else [],
            "order_counts": [int(v) for v in df["order_count"]] if not df.empty else [],
            "total_qty": [int(v) for v in df["total_qty"]] if not df.empty else [],
            "total_sales": [float(v) for v in df["total_sales"]] if not df.empty else [],
        }
    except Exception as e:
        return {"categories": [], "order_counts": [], "total_qty": [], "total_sales": [], "error": str(e)}


@router.get("/dashboard/boxplot-data")
async def get_boxplot_data(db=Depends(get_db)):
    """品类销售额分布统计 (箱线图: min/q1/median/q3/max per category)"""
    tables = db.get_table_names()
    if "orders" not in tables or "products" not in tables:
        return {"labels": [], "values": []}
    try:
        df = db.query("""
            SELECT p.category, o.total_amount
            FROM orders o
            JOIN products p ON o.product_id = p.product_id
            WHERE o.status = '已完成'
            ORDER BY p.category, o.total_amount
        """)
        if df.empty:
            return {"labels": [], "values": []}

        # Python-side percentile computation for cross-DB compatibility
        import numpy as np
        grouped = df.groupby("category")["total_amount"]
        labels = []
        values = []
        for cat, group in grouped:
            arr = group.values
            if len(arr) < 2:
                continue
            labels.append(cat)
            q1, q2, q3 = np.percentile(arr, [25, 50, 75])
            values.append([
                float(arr.min()),
                float(round(q1, 2)),
                float(round(q2, 2)),
                float(round(q3, 2)),
                float(arr.max()),
            ])
        return {"labels": labels, "values": values}
    except Exception as e:
        return {"labels": [], "values": [], "error": str(e)}


@router.get("/dashboard/region-analysis")
async def get_region_analysis(region_filter: str = "全部", db=Depends(get_db)):
    """地区销售分析数据"""
    tables = db.get_table_names()
    if "orders" not in tables or "users" not in tables:
        return {"provinces": [], "order_counts": [], "total_sales": [], "avg_amount": []}
    region_where = f"AND u.province = '{region_filter}'" if region_filter != "全部" else ""
    try:
        df = db.query(f"""
            SELECT u.province,
                   COUNT(DISTINCT o.order_id) AS order_count,
                   ROUND(SUM(o.total_amount), 2) AS total_sales,
                   ROUND(AVG(o.total_amount), 2) AS avg_amount
            FROM orders o
            JOIN users u ON o.user_id = u.user_id
            WHERE o.status = '已完成' {region_where}
            GROUP BY u.province
            ORDER BY total_sales DESC
            LIMIT 15
        """)
        return {
            "provinces": df["province"].tolist() if not df.empty else [],
            "order_counts": [int(v) for v in df["order_count"]] if not df.empty else [],
            "total_sales": [float(v) for v in df["total_sales"]] if not df.empty else [],
            "avg_amount": [float(v) for v in df["avg_amount"]] if not df.empty else [],
        }
    except Exception as e:
        return {"provinces": [], "order_counts": [], "total_sales": [], "avg_amount": [], "error": str(e)}


@router.get("/dashboard/detail-table")
async def get_detail_table(db=Depends(get_db)):
    """订单明细数据"""
    tables = db.get_table_names()
    if "orders" not in tables:
        return {"columns": [], "rows": []}
    try:
        if "users" in tables and "products" in tables:
            df = db.query("""
                SELECT o.order_id, o.order_date, u.username,
                       p.product_name, p.category AS category,
                       o.quantity, o.total_amount AS amount,
                       o.status, o.payment_method
                FROM orders o
                JOIN users u ON o.user_id = u.user_id
                JOIN products p ON o.product_id = p.product_id
                ORDER BY o.order_date DESC
                LIMIT 100
            """)
        else:
            df = db.query("SELECT * FROM orders ORDER BY order_date DESC LIMIT 100")
        return {
            "columns": df.columns.tolist() if not df.empty else [],
            "rows": df.values.tolist() if not df.empty else [],
        }
    except Exception as e:
        return {"columns": [], "rows": [], "error": str(e)}


# ============================================================================
# 缓存
# ============================================================================

@router.get("/cache/stats")
async def get_cache_stats(cache=Depends(get_cache)):
    """缓存统计"""
    try:
        return cache.get_stats()
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cache/clear")
async def clear_cache(cache=Depends(get_cache)):
    """清空缓存"""
    try:
        cache.clear()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/vector-store/rebuild")
async def rebuild_vector_store():
    """重建向量索引"""
    try:
        from core.vector_store import SchemaVectorStore
        vs = SchemaVectorStore()
        vs.build_index()
        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# 自定义图表
# ============================================================================

@router.get("/dashboard/tables")
async def list_tables(db=Depends(get_db)):
    """获取当前数据库的所有表名"""
    try:
        tables = db.get_table_names()
        return {"tables": tables}
    except Exception as e:
        return {"tables": [], "error": str(e)}


@router.get("/dashboard/table-schema")
async def get_table_schema(table: str, db=Depends(get_db)):
    """获取指定表的列信息"""
    try:
        from sqlalchemy import inspect as sa_inspect
        inspector = sa_inspect(db.engine)
        columns = inspector.get_columns(table)
        return {
            "columns": [
                {"name": col["name"], "type": str(col["type"])}
                for col in columns
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


class ChartDataRequest(BaseModel):
    table: str
    x_column: str
    y_column: str
    chart_type: str = "bar"
    limit: int = 1000
    aggregation: Optional[str] = None
    custom_sql: Optional[str] = None


@router.post("/dashboard/chart-data")
async def get_chart_data(body: ChartDataRequest, db=Depends(get_db)):
    """获取图表数据（支持自定义 SQL、多表 JOIN、聚合）"""
    try:
        # 优先使用自定义 SQL
        if body.custom_sql:
            df = db.query(body.custom_sql)
            if df.empty or len(df.columns) < 2:
                return {"labels": [], "values": [], "error": "无数据"}
            x_col = df.columns[0]
            y_col = df.columns[1]
            return {
                "labels": df[x_col].tolist() if x_col in df else [],
                "values": [float(v) for v in df[y_col]] if y_col in df and df[y_col].dtype in ("float64", "int64") else (df[y_col].tolist() if y_col in df else []),
                "columns": [{"name": col, "type": str(df[col].dtype)} for col in df.columns],
            }

        x_col = body.x_column
        y_col = body.y_column
        table = body.table
        limit = body.limit

        if db.active_db_type == "mysql":
            qtable = f"`{table}`"
        else:
            qtable = f"\"{table}\""

        # 支持聚合
        if body.aggregation:
            sql = f"SELECT {x_col}, {body.aggregation}({y_col}) as {y_col} FROM {qtable} WHERE {x_col} IS NOT NULL AND {y_col} IS NOT NULL GROUP BY {x_col} ORDER BY {x_col} LIMIT {limit}"
        else:
            sql = f"SELECT {x_col}, {y_col} FROM {qtable} WHERE {x_col} IS NOT NULL AND {y_col} IS NOT NULL ORDER BY {x_col} LIMIT {limit}"

        df = db.query(sql)

        if df.empty:
            return {"labels": [], "values": [], "error": "无数据"}

        return {
            "labels": df[x_col].tolist(),
            "values": [float(v) for v in df[y_col]] if df[y_col].dtype in ("float64", "int64") else df[y_col].tolist(),
            "columns": [{"name": col, "type": str(df[col].dtype)} for col in df.columns],
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# 文件上传
# ============================================================================

# 最大上传大小 50MB
_MAX_UPLOAD_SIZE = 50 * 1024 * 1024

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传 CSV/Excel 文件，返回 schema + 预览，并将数据写入数据库"""
    if not file.filename:
        raise HTTPException(400, "未选择文件")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".csv", ".xlsx", ".xls", ".pdf"):
        raise HTTPException(400, f"不支持的文件格式: {ext}")

    try:
        from services.file_processor import FileProcessor
        from core.database import get_db
        import re

        contents = await file.read()
        if len(contents) > _MAX_UPLOAD_SIZE:
            raise HTTPException(400, f"文件过大（{len(contents)/1024/1024:.1f}MB），最大 50MB")
        processor = FileProcessor()
        result = processor.process(contents, file.filename)
        if result.error:
            raise HTTPException(400, result.error)

        # 插入数据到数据库（仅 CSV/Excel）
        db = get_db()
        tables_created = []

        def sanitize_name(name: str) -> str:
            """将文件名/表名转为合法 SQL 标识符"""
            name = re.sub(r'\.[^.]+$', '', name)  # 去掉扩展名
            name = re.sub(r'[^\w一-鿿]+', '_', name)
            name = re.sub(r'^_+|_+$', '', name)
            if not name or name[0].isdigit():
                name = 't_' + name
            return name or 'uploaded_table'

        if ext != ".pdf":
            for sheet_name, df in result.sheets.items():
                safe_name = sanitize_name(f"{result.filename}_{sheet_name}" if len(result.sheets) > 1 else result.filename)
                # 写入数据库
                df.to_sql(safe_name, db.engine, if_exists="replace", index=False)
                tables_created.append(safe_name)
                logger.info(f"[上传] 创建表 {safe_name} ({len(df)} 行)")

        sheets = {}
        for name, df in result.sheets.items():
            sheets[name] = {
                "columns": df.columns.tolist(),
                "dtypes": {str(k): str(v) for k, v in df.dtypes.items()},
                "preview_rows": df.head(10).values.tolist(),
                "row_count": len(df),
            }

        return {
            "filename": result.filename,
            "file_type": result.file_type,
            "row_count": result.row_count,
            "summary": result.summary,
            "sheets": sheets,
            "tables_created": tables_created,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, detail=str(e))


@router.delete("/upload")
async def clear_upload():
    """清除上传的文件（由前端管理状态，服务端仅确认）"""
    return {"success": True}


@router.post("/upload/stream")
async def upload_file_stream(file: UploadFile = File(...)):
    """
    SSE 流式文件上传。解析 CSV/Excel/PDF，分阶段推送 progress，超时 60s。

    事件:
      progress: { step: "reading"|"parsing"|"writing"|"done", message, percent }
      result:   { filename, tables_created, sheets }
      error:    { error }
      done:     {}
    """
    import re
    from services.file_processor import FileProcessor
    from core.database import get_db as _get_db

    if not file.filename:
        return JSONResponse(status_code=400, content={"error": "未选择文件"})

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".csv", ".xlsx", ".xls", ".pdf"):
        return JSONResponse(status_code=400, content={"error": f"不支持的文件格式: {ext}"})

    async def event_stream():
        sent_done = False
        try:
            yield _sse("progress", {"step": "reading", "message": "读取文件中...", "percent": 10})

            # 流式读取（限制 50MB）
            total = 0
            chunks = []
            while True:
                chunk = await asyncio.wait_for(file.read(8192), timeout=60)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_UPLOAD_SIZE:
                    yield _sse("error", {"error": f"文件过大（{total/1024/1024:.1f}MB），最大 50MB"})
                    return
            contents = b"".join(chunks)
            yield _sse("progress", {"step": "parsing", "message": "解析文件中...", "percent": 40})

            processor = FileProcessor()
            result = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(None, processor.process, contents, file.filename),
                timeout=120,
            )
            if result.error:
                yield _sse("error", {"error": result.error})
                return

            yield _sse("progress", {"step": "writing", "message": "写入数据库...", "percent": 70})

            # 写入临时 SQLite 表
            db = _get_db()
            tables_created = []

            def sanitize_name(name: str) -> str:
                name = re.sub(r'\.[^.]+$', '', name)
                name = re.sub(r'[^\w一-鿿]+', '_', name)
                name = re.sub(r'^_+|_+$', '', name)
                if not name or name[0].isdigit():
                    name = 't_' + name
                return name or 'uploaded_table'

            if ext != ".pdf":
                for sheet_name, df in result.sheets.items():
                    safe_name = sanitize_name(f"{result.filename}_{sheet_name}" if len(result.sheets) > 1 else result.filename)
                    df.to_sql(safe_name, db.engine, if_exists="replace", index=False)
                    tables_created.append(safe_name)
                    logger.info(f"[上传] 创建表 {safe_name} ({len(df)} 行)")

            sheets = {}
            for name, df in result.sheets.items():
                sheets[name] = {
                    "columns": df.columns.tolist(),
                    "dtypes": {str(k): str(v) for k, v in df.dtypes.items()},
                    "preview_rows": df.head(10).values.tolist(),
                    "row_count": len(df),
                }

            yield _sse("result", {
                "filename": result.filename,
                "file_type": result.file_type,
                "row_count": result.row_count,
                "summary": result.summary,
                "sheets": sheets,
                "tables_created": tables_created,
            })
            yield _sse("done", {})
            sent_done = True

        except asyncio.TimeoutError:
            logger.error("[upload-stream] 处理超时")
            yield _sse("error", {"error": "处理超时（60s），文件过大或解析耗时过长"})
        except Exception as e:
            logger.error(f"[upload-stream] 异常: {e}", exc_info=True)
            yield _sse("error", {"error": str(e)[:200]})
        finally:
            if not sent_done:
                try:
                    yield _sse("done", {})
                except GeneratorExit:
                    pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================================
# 数据库连接管理
# ============================================================================

@router.get("/dashboard/table-preview")
async def get_table_preview(table: str, db=Depends(get_db)):
    """获取表的前 20 行预览数据"""
    try:
        # Validate table exists (but don't trust get_table_names blindly — some DBs may fail silently)
        tables = db.get_table_names()
        if tables and table not in tables:
            avail = ", ".join(tables) if tables else "无（请先连接数据库）"
            raise HTTPException(
                status_code=400,
                detail=f"表 '{table}' 不存在。当前数据库 ({db.active_db_type}) 中的表: [{avail}]",
            )
        # Properly quote table name based on DB type (MySQL uses backticks)
        if db.active_db_type == "mysql":
            quoted = f"`{table}`"
        else:
            quoted = f"\"{table}\""
        df = db.query(f"SELECT * FROM {quoted} LIMIT 20")
        return {
            "columns": df.columns.tolist() if not df.empty else [],
            "rows": df.values.tolist() if not df.empty else [],
            "row_count": len(df),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/db/test-connection")
async def test_db_connection(body: DbConnectionRequest):
    """测试数据库连接是否可用"""
    try:
        result = DatabaseManager.test_connection(
            db_type=body.db_type,
            host=body.host,
            port=body.port,
            database=body.database,
            user=body.user,
            password=body.password,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/db/connect")
async def connect_db(body: DbConnectionRequest, db=Depends(get_db)):
    """切换到新的数据库连接"""
    try:
        db.switch_connection(
            db_type=body.db_type,
            host=body.host,
            port=body.port,
            database=body.database,
            user=body.user,
            password=body.password,
        )
        # 获取表信息
        tables = db.get_table_names()
        return {
            "success": True,
            "db_type": body.db_type,
            "tables": tables,
            "message": f"已成功连接到 {body.db_type} 数据库",
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/db/status")
async def get_db_status(db=Depends(get_db)):
    """获取当前数据库连接状态"""
    try:
        # 主动触发懒加载引擎创建（确保首次调用时能检测到连接）
        try:
            _ = db.engine
        except Exception:
            pass
        info = db.get_connection_info()
        tables = db.get_table_names() if info["connected"] else []
        info["active_tables"] = len(tables)
        info["tables"] = tables
        return info
    except Exception as e:
        return {
            "connected": False,
            "error": str(e),
            "active_tables": 0,
            "tables": [],
        }


@router.get("/db/suggest-questions")
async def suggest_questions():
    """
    根据当前数据库表结构，优先通过 LLM 推荐 3 个自然语言问题。

    前端 Chat 和连接数据库后调用，展示为快捷问题按钮。
    基于真实 Schema 的字段名和注释生成，不使用预设示例。
    """
    from core.database import get_db
    from core.config import CONFIG
    db = get_db()

    try:
        table_info = db.get_table_info()
    except Exception:
        table_info = []

    if not table_info:
        return {"questions": []}

    # 优先使用 LLM 生成
    if CONFIG.llm.openai_api_key:
        try:
            from core.llm_client import BaseLLMClient
            client = BaseLLMClient(
                model=CONFIG.llm.generator_model,
                name="suggest-questions",
            )
            # 构建 schema 摘要
            parts = []
            for t in table_info[:5]:
                cols = [f"{c['name']}({c['type']})" + (f" — {c['comment']}" if c.get('comment') else "") for c in t["columns"][:8]]
                parts.append(f"表 {t['table_name']}({t.get('row_count', 0)}行): " + ", ".join(cols))
            schema_text = "\n".join(parts)

            result = client.generate_json(
                "根据以下数据库表结构，推荐 3 个用户最可能问的自然语言数据分析问题。"
                "要求：问题贴合实际业务场景，覆盖不同表。"
                "只返回 JSON 数组格式如 [\"问题1\", \"问题2\", \"问题3\"]，不要其他内容。\n\n"
                f"数据库表结构：\n{schema_text}",
                system_prompt="你是一名资深数据分析师。",
            )
            if result and isinstance(result, list) and len(result) >= 3:
                return {"questions": result[:3]}
        except Exception:
            pass

    # LLM 不可用时基于 schema 实际字段名生成
    questions = []
    for t in table_info[:5]:
        tbl = t["table_name"]
        cols = [c["name"] for c in t["columns"]]
        if len(questions) >= 3:
            break
        # 找第一个字符串列 + 数值列组合 -> 统计类问题
        str_col = next((c for c in cols if any(k in c.lower() for k in ('name', 'type', 'city', 'province', 'category', 'status', 'gender'))), cols[0] if cols else None)
        num_col = next((c for c in cols if any(k in c.lower() for k in ('amount', 'price', 'count', 'sales', 'revenue', 'age', 'score'))), None)
        if str_col and num_col:
            questions.append(f"按{tbl}的{str_col}统计{num_col}")
        elif str_col:
            questions.append(f"查看{tbl}的{str_col}分布")
        elif num_col:
            questions.append(f"分析{tbl}的{num_col}趋势")
        else:
            questions.append(f"浏览{tbl}的全部数据（{t.get('row_count', 0)} 条）")
    while len(questions) < 3:
        questions.append("查看所有表的记录数")

    return {"questions": questions[:3]}


@router.post("/db/reset")
async def reset_db_connection(db=Depends(get_db)):
    """重置为环境变量配置的默认数据库连接"""
    try:
        db.reset_connection()
        # 获取当前可用表（不创建或播种任何数据）
        tables = db.get_table_names()
        return {
            "success": True,
            "message": "已重置为默认数据库连接",
            "tables": tables,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# 评估报告 & SSE 实时评估
# ============================================================================

# 当前评估进度（供 /api/eval/status 轮询读取）
_eval_progress = {"running": False, "total": 0, "completed": 0, "current": "", "results": []}

@router.get("/eval/report")
async def get_eval_report():
    """获取评估报告"""
    report_path = "eval_results/eval_report.json"
    if os.path.exists(report_path):
        try:
            with open(report_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            return {"error": f"报告读取失败: {e}"}
    return {"error": "尚未运行评估，请在终端执行: python evaluator.py"}


@router.get("/eval/status")
async def get_eval_status():
    """获取当前评估进度（SSE 降级轮询用）"""
    return _eval_progress


@router.get("/eval/run-stream")
async def eval_run_stream():
    """
    SSE 实时评估流。

    事件:
      progress: { completed, total, current, category }
      result:   { id, category, question, sql_valid, execution_match }
      error:    { case_id, error }
      done:     { report }
    """
    from services.evaluator import Evaluator
    from agent import TextToSQLAgent

    async def event_stream():
        global _eval_progress
        agent = TextToSQLAgent()
        evaluator = Evaluator()
        dataset = evaluator.dataset
        _SINGLE_TIMEOUT = 120  # 单个用例超时秒数

        _eval_progress = {"running": True, "total": len(dataset), "completed": 0, "current": "", "results": []}
        results = []
        sent_done = False

        try:
            for i, case in enumerate(dataset, 1):
                current_label = f"[{i}/{len(dataset)}] {case.get('category','')}: {case.get('question','')[:30]}..."
                _eval_progress["current"] = current_label

                yield _sse("progress", {
                    "completed": i - 1,
                    "total": len(dataset),
                    "current": current_label,
                    "category": case.get("category", ""),
                })

                # 异步执行单个评估，带超时
                try:
                    loop = asyncio.get_event_loop()
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, evaluator._evaluate_single, agent, case),
                        timeout=_SINGLE_TIMEOUT,
                    )
                except asyncio.TimeoutError:
                    logger.error(f"[eval-stream] 用例 {case.get('id',i)} 超时 ({_SINGLE_TIMEOUT}s)")
                    result = {
                        "id": case.get("id", i),
                        "category": case.get("category", ""),
                        "question": case.get("question", ""),
                        "sql_valid": False, "execution_match": False,
                        "error": f"执行超时 ({_SINGLE_TIMEOUT}s)",
                    }
                    yield _sse("error", {"case_id": case.get("id", i), "error": result["error"]})

                results.append(result)
                _eval_progress["completed"] = i
                _eval_progress["results"] = results

                status = "✓" if result.get("sql_valid") and result.get("execution_match") else "✗"
                yield _sse("result", {
                    "id": case.get("id", i),
                    "category": case.get("category", ""),
                    "question": case.get("question", "")[:50],
                    "sql_valid": result.get("sql_valid", False),
                    "execution_match": result.get("execution_match", False),
                    "status": status,
                    "generated_sql": result.get("generated_sql", ""),
                    "execution_time": result.get("execution_time", 0),
                })

            # 生成报告
            evaluator.results = results
            report = evaluator.generate_report()
            _eval_progress["running"] = False
            _eval_progress["report"] = report

            yield _sse("done", {"report": report})
            sent_done = True

        except asyncio.CancelledError:
            logger.warning("[eval-stream] 客户端断开")
            _eval_progress["running"] = False

        except Exception as e:
            logger.error(f"[eval-stream] 评估异常: {e}", exc_info=True)
            _eval_progress["running"] = False
            try:
                yield _sse("error", {"error": str(e)[:200]})
            except GeneratorExit:
                pass

        finally:
            # 确保始终发送 done 事件，防止 ERR_INCOMPLETE_CHUNKED_ENCODING
            if not sent_done:
                try:
                    yield _sse("done", {})
                except GeneratorExit:
                    pass
            logger.info(f"[eval-stream] 流结束 ({len(results)}/{len(dataset)} 用例)")

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
