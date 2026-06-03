"""
==============================================================================
API 路由 — 所有后端接口
==============================================================================
"""

import json
import os
import logging
from fastapi import APIRouter, HTTPException, UploadFile, File, Depends
from fastapi.responses import StreamingResponse

from pydantic import BaseModel
from api.schemas import ChatRequest, DashboardControlRequest, DbConnectionRequest
from api.dependencies import get_agent, get_db, get_cache
from api.streaming import stream_chat
from database import DatabaseManager

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
    """SSE 流式聊天"""
    return StreamingResponse(
        stream_chat(body.question),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================================
# 数据大屏 — KPI
# ============================================================================

@router.get("/kpi")
async def get_kpi(db=Depends(get_db)):
    """KPI 指标卡数据"""
    try:
        total_users = int(db.query("SELECT COUNT(*) as cnt FROM users").iloc[0]["cnt"])
        total_orders = int(db.query("SELECT COUNT(*) as cnt FROM orders").iloc[0]["cnt"])
        total_products = int(db.query("SELECT COUNT(*) as cnt FROM products").iloc[0]["cnt"])
        total_revenue = float(db.query(
            "SELECT COALESCE(SUM(total_amount), 0) as total FROM orders WHERE status = '已完成'"
        ).iloc[0]["total"])
        return {
            "total_users": total_users,
            "total_orders": total_orders,
            "total_products": total_products,
            "total_revenue": total_revenue,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/sales-trend")
async def get_sales_trend(view_mode: str = "daily", db=Depends(get_db)):
    """销售趋势数据"""
    try:
        if view_mode == "monthly":
            df = db.query("""
                SELECT strftime('%Y-%m', order_date) AS period,
                       SUM(total_amount) AS sales,
                       COUNT(*) AS orders
                FROM orders WHERE status = '已完成'
                GROUP BY period ORDER BY period
            """)
        else:
            df = db.query("""
                SELECT order_date AS period,
                       SUM(total_amount) AS sales,
                       COUNT(*) AS orders
                FROM orders WHERE status = '已完成'
                GROUP BY order_date ORDER BY order_date
            """)
        return {
            "periods": df["period"].tolist() if not df.empty else [],
            "sales": [float(v) for v in df["sales"]] if not df.empty else [],
            "orders": [int(v) for v in df["orders"]] if not df.empty else [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/category-analysis")
async def get_category_analysis(db=Depends(get_db)):
    """商品类别分析数据"""
    try:
        df = db.query("""
            SELECT p.category,
                   COUNT(DISTINCT o.order_id) AS order_count,
                   SUM(o.quantity) AS total_qty,
                   ROUND(SUM(o.total_amount), 2) AS total_sales
            FROM orders o
            JOIN products p ON o.product_id = p.product_id
            WHERE o.status = '已完成'
            GROUP BY p.category
            ORDER BY total_sales DESC
        """)
        return {
            "categories": df["category"].tolist() if not df.empty else [],
            "order_counts": [int(v) for v in df["order_count"]] if not df.empty else [],
            "total_qty": [int(v) for v in df["total_qty"]] if not df.empty else [],
            "total_sales": [float(v) for v in df["total_sales"]] if not df.empty else [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/boxplot-data")
async def get_boxplot_data(db=Depends(get_db)):
    """品类销售额分布统计 (箱线图: min/q1/median/q3/max per category)"""
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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/region-analysis")
async def get_region_analysis(region_filter: str = "全部", db=Depends(get_db)):
    """地区销售分析数据"""
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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/dashboard/detail-table")
async def get_detail_table(db=Depends(get_db)):
    """订单明细数据"""
    try:
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
        return {
            "columns": df.columns.tolist() if not df.empty else [],
            "rows": df.values.tolist() if not df.empty else [],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
        from vector_store import SchemaVectorStore
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


@router.post("/dashboard/chart-data")
async def get_chart_data(body: ChartDataRequest, db=Depends(get_db)):
    """获取图表数据（按 X/Y 列从表中查询）"""
    try:
        x_col = body.x_column
        y_col = body.y_column
        table = body.table
        limit = body.limit

        # 对 y_column 做聚合（按 x_column 分组）
        if db.active_db_type == "mysql":
            qtable = f"`{table}`"
        else:
            qtable = f"\"{table}\""
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

@router.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    """上传 CSV/Excel 文件，返回 schema + 预览，并将数据写入数据库"""
    if not file.filename:
        raise HTTPException(400, "未选择文件")

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in (".csv", ".xlsx", ".xls", ".pdf"):
        raise HTTPException(400, f"不支持的文件格式: {ext}")

    try:
        from file_processor import FileProcessor
        from database import get_db
        import re

        contents = await file.read()
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


# ============================================================================
# 评估报告
# ============================================================================

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


@router.post("/db/reset")
async def reset_db_connection(db=Depends(get_db)):
    """重置为环境变量配置的默认数据库连接"""
    try:
        db.reset_connection()
        # 重新初始化和播种
        db.initialize()
        tables = db.get_table_names()
        return {
            "success": True,
            "message": "已重置为默认数据库连接",
            "tables": tables,
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


# ============================================================================
# 评估报告
# ============================================================================

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
