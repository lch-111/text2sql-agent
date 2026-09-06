"""
==============================================================================
mcp_server.py — Text2SQL MCP Server（stdio / FastMCP 独立入口）
==============================================================================
定位：
  在不改动现有核心代码的前提下，把 Text2SQL 引擎的能力通过 MCP 暴露给
  Claude Desktop / Claude Code 等 MCP 客户端。仅新增本文件。

暴露的 3 个工具（对齐“瑞幸点单拆工具”思路）：
  1. query_db(question)   —— 自然语言查询数据库（走完整 Text-to-SQL 链路）
  2. get_schema(table_name?) —— 查看表结构（列名/类型/主键/可空）
  3. explain_sql(sql)     —— 解读 SQL 执行计划（安全把关 + 真实 EXPLAIN + 规则建议）

设计原则：
  - 不 import 即失败：仅当 `mcp` SDK 可用时才注册工具；模块本身在缺 mcp 时
    仍可 import（便于在无 SDK 环境做语法/逻辑自检）。
  - 日志一律走 stderr（stdio 协议只占 stdout，严禁污染）。
  - SQL 安全沿用 SQLGuard；执行计划只允许 SELECT/WITH（与项目安全红线一致）。
  - 复用现有核心入口（见各函数 docstring），不修改任何既有文件。

用法：
  python mcp_server.py                 # 以 MCP stdio 服务运行（给 Claude Desktop）
  python mcp_server.py --self-test     # 脱离 MCP 的自检（打印 3 个工具的调用样例）
==============================================================================
"""

from __future__ import annotations

import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

# 保证从任意工作目录运行都能 import 本项目包（core / agents / services ...）
_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# ---- 日志：一律送 stderr，避免污染 stdio 的 stdout 通道 ----
logging.basicConfig(
    stream=sys.stderr,
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
# 压掉第三方库的 INFO 噪音（httpx 每次请求都会打 INFO；mcp SDK 也压到 WARNING）
for _noisy in ("httpx", "httpcore", "urllib3", "mcp", "anyio"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("mcp_server")

# ============================================================================
# MCP SDK 兼容导入（v1.x FastMCP / v2.x MCPServer 通用；缺包仍可 import）
#   - mcp SDK < 2：经典入口 `mcp.server.fastmcp.FastMCP`
#   - mcp SDK >= 2：FastMCP 更名为 `mcp.server.mcpserver.MCPServer`
#   两者关键用法一致（name/instructions 构造 + @.tool() 装饰 + run("stdio")），
#   故只需在导入层做一次别名映射，注册代码无需分支。
# ============================================================================
_MCP_SERVER_CLASS = None  # 实例化用到的 Server 类（由导入分支决定）
try:
    from mcp.server.fastmcp import FastMCP as _MCP_SERVER_CLASS
except Exception as _exc_fastmcp:  # noqa: BLE001 —— v1 入口不存在则试 v2
    try:
        from mcp.server.mcpserver import MCPServer as _MCP_SERVER_CLASS
    except Exception as _exc_mcpserver:  # noqa: BLE001 —— 两代都没有 → 无 SDK
        _MCP_SERVER_CLASS = None
        _MCP_IMPORT_REASON = "fastmcp: %s | mcpserver: %s" % (_exc_fastmcp, _exc_mcpserver)
        logger.warning("未检测到 mcp SDK，工具注册被跳过（原因: %s）", _MCP_IMPORT_REASON)

_HAS_MCP = _MCP_SERVER_CLASS is not None

# 结果行/单元格的裁剪上限，避免超大结果撑爆对话上下文
_MAX_ROWS = 200
_MAX_CELL = 300
_MAX_PLAN_ROWS = 50


# ============================================================================
# 通用工具函数
# ============================================================================

def _to_plain(obj: Any) -> Any:
    """把 pandas/numpy/Decimal 等结果转成可 JSON 序列化的纯 Python 对象。

    递归处理列表/字典；NaN/NaT/None → None；超长字符串截断。
    """
    import math

    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(x) for x in obj[: _MAX_ROWS]]
    # pandas/numpy 标量
    if hasattr(obj, "item") and callable(getattr(obj, "item")):
        try:
            obj = obj.item()
        except (ValueError, OverflowError, TypeError):
            pass
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, (str, int, bool)) or obj is None:
        if isinstance(obj, str) and len(obj) > _MAX_CELL:
            return obj[:_MAX_CELL] + "…"
        return obj
    # datetime / Decimal / 其它 → 字符串兜底
    try:
        s = str(obj)
        return s if len(s) <= _MAX_CELL else s[:_MAX_CELL] + "…"
    except Exception:  # noqa: BLE001
        return "<不可序列化>"


def _dump(obj: Any) -> str:
    """安全序列化为 JSON 字符串（_to_plain 兜底后不可能再抛异常）。"""
    return json.dumps(_to_plain(obj), ensure_ascii=False, default=str)


def _lazy_agent():
    """延迟构造 TextToSQLAgent（避免模块 import 时加载重依赖）。"""
    from core.agent import TextToSQLAgent

    return TextToSQLAgent()


def _lazy_db():
    """延迟获取数据库管理器单例（env 或 data/connection.json 持久化连接）。"""
    from core.database import get_db

    return get_db()


def _lazy_guard():
    """延迟获取 SQLGuard 安全拦截器。"""
    from harness.sql_guard import SQLGuard

    return SQLGuard()


def _lazy_optimizer():
    """延迟获取 SQL 优化器单例（规则式执行计划解读）。"""
    from services.sql_optimizer import get_optimizer

    return get_optimizer()


def _first_line(text: str) -> str:
    """取首行（用于日志/摘要）。"""
    return (text or "").strip().splitlines()[0] if text else ""


# ============================================================================
# 工具实现（纯函数，便于自检/测试；MCP 装饰器只做薄封装）
# ============================================================================

def query_db(question: str) -> Dict[str, Any]:
    """自然语言查询数据库（Text-to-SQL 全链路）。

    复用入口: core.agent.TextToSQLAgent.run()
      （LangGraph：字段映射 → Schema 检索 → SQL 生成 → SQLGuard 拦截 → 只读执行）

    参数:
        question: 用户的自然语言问题，如 "按省份统计销售额 TOP5"

    返回:
        {
          "ok": bool,
          "question": str,
          "sql": str | None,          # 生成的 SQL
          "row_count": int,           # 返回行数（0 表示空结果）
          "columns": [str, ...] | None,
          "result": [dict, ...] | None,  # 数据行（最多 _MAX_ROWS 行，单元格截断）
          "clarification": str | None,   # 需要澄清时给出提示（sql 为空）
          "cache_hit": bool,
          "error": str | None,           # 失败时的中文错误说明
        }
    """
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "question 不能为空"}

    logger.info("[query_db] 收到问题: %s", _first_line(question))
    try:
        agent = _lazy_agent()
        res = agent.run(question=question, use_cache=True, conv_id="mcp")
    except Exception as exc:  # noqa: BLE001 —— 把任何异常变成友好错误返回
        logger.error("[query_db] 执行异常: %s", exc)
        return {"ok": False, "error": f"Text-to-SQL 执行失败: {exc}"}

    sql = res.get("sql") or ""
    error = res.get("error")
    clarification = res.get("clarification")

    # 链路要求澄清（问题歧义，无 SQL）时原样转给上层
    if not sql:
        return {
            "ok": False,
            "question": question,
            "sql": None,
            "result": None,
            "columns": None,
            "row_count": 0,
            "clarification": clarification or error or "无法生成 SQL",
            "cache_hit": bool(res.get("cache_hit")),
            "error": error,
        }

    rows = res.get("result") or []
    cols = res.get("columns") or []
    logger.info("[query_db] 生成 SQL，返回 %d 行 (cache_hit=%s)", len(rows), res.get("cache_hit"))
    return {
        "ok": not error,
        "question": question,
        "sql": sql,
        "row_count": len(rows),
        "columns": _to_plain(cols) if cols else None,
        "result": _to_plain(rows[: _MAX_ROWS]),
        "truncated": len(rows) > _MAX_ROWS,
        "clarification": None,
        "cache_hit": bool(res.get("cache_hit")),
        "execution_time": res.get("execution_time"),
        "retries": res.get("retries"),
        "error": error,
    }


def get_schema(table_name: Optional[str] = None) -> Dict[str, Any]:
    """查看数据库表结构（只读）。

    复用入口: core.database.DatabaseManager
      （get_table_names + SQLAlchemy Inspector.get_columns / get_pk_constraint）

    说明：刻意不复用 get_table_info()，因其会对每张表跑 COUNT/样例/去重统计，
    大表上慢且 SQLite 引号写法在 MySQL 下不可用；这里做轻量、跨引擎的列级读取。

    参数:
        table_name: 表名（可选）。传了则精确匹配；不传或找不到则返回全部表名清单。

    返回:
        {
          "ok": bool,
          "db_type": str,
          "matched": [{ "table_name", "columns":[{"name","type","nullable"}],
                        "primary_key" }, ...],   # 找到的表结构
          "available_tables": [str, ...],         # 全部表名（供参考）
          "error": str | None,
        }
    """
    try:
        db = _lazy_db()
        tables = db.get_table_names()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"无法连接数据库（请检查 DB_* 环境变量或已保存连接）: {exc}", "db_type": _safe_db_type()}

    if not tables:
        return {"ok": False, "db_type": _safe_db_type(), "matched": [], "available_tables": [], "error": "当前数据库没有任何表"}

    # 定位目标表：精确匹配优先，其次大小写/包含匹配，再退回第一张表
    target = None
    if table_name and table_name.strip():
        name = table_name.strip()
        if name in tables:
            target = name
        else:
            lower = name.lower()
            hit = next((t for t in tables if t.lower() == lower), None)
            if hit is None:
                hit = next((t for t in tables if lower in t.lower()), None)
            target = hit

    def _meta(tbl: str) -> Optional[Dict[str, Any]]:
        """单表列级元数据。"""
        try:
            from sqlalchemy import inspect as sa_inspect

            inspector = sa_inspect(db.engine)
            cols = inspector.get_columns(tbl)
            pk_cols = (inspector.get_pk_constraint(tbl) or {}).get("constrained_columns", []) or []
            return {
                "table_name": tbl,
                "primary_key": pk_cols[0] if pk_cols else None,
                "columns": [
                    {
                        "name": c["name"],
                        "type": str(c["type"]),
                        "nullable": bool(c.get("nullable", True)),
                        "default": c.get("default"),
                    }
                    for c in cols
                ],
            }
        except Exception as exc:  # noqa: BLE001
            logger.warning("[get_schema] 读取表 %s 失败: %s", tbl, exc)
            return None

    matched: List[Dict[str, Any]] = []
    note = None
    if target:
        m = _meta(target)
        if m:
            matched.append(m)
        else:
            note = f"表 {target} 元数据读取失败"
    if not matched:
        # 兜底：只返回前若干张表的轻量结构，避免响应过大
        for tbl in tables[:_MAX_ROWS]:
            m = _meta(tbl)
            if m:
                matched.append(m)

    logger.info("[get_schema] 返回 %d 张表结构", len(matched))
    return {
        "ok": True,
        "db_type": _safe_db_type(),
        "matched": matched,
        "available_tables": tables,
        "error": note,
    }


def explain_sql(sql: str) -> Dict[str, Any]:
    """解释一条 SQL 的执行计划（只读，不执行 DML）。

    复用入口:
      - harness.sql_guard.SQLGuard.validate()  安全拦截（只放行 SELECT/WITH）
      - core.database.DatabaseManager.query_readonly()  执行 EXPLAIN（只读）
      - services.sql_optimizer.SQLOptimizer.analyze()   规则式优化建议

    参数:
        sql: 只读 SQL（SELECT / WITH 开头），如 "SELECT * FROM orders WHERE price>100"

    返回:
        {
          "ok": bool,
          "db_type": str,
          "plan": {"columns": [str,...], "rows": [dict,...]} | None,  # 真实执行计划
          "suggestions": [{"id","message","severity"}, ...],          # 规则建议
          "indexes": [{"field","suggestion"}, ...],
          "optimized_sql": str | None,
          "error": str | None,
        }
    """
    sql = (sql or "").strip()
    if not sql:
        return {"ok": False, "error": "sql 不能为空"}

    stripped = sql.rstrip(";").strip()
    head = stripped.lstrip().upper()
    # 安全红线：只允许只读查询（与 SQLGuard / 生成器约束一致）
    if not (head.startswith("SELECT") or head.startswith("WITH")):
        return {
            "ok": False,
            "error": "仅支持解释只读查询（SELECT / WITH 开头）；为安全红线不允许对写操作生成执行计划",
        }

    # 1) SQLGuard 物理安全拦截（拦截写操作/危险语句）
    try:
        _lazy_guard().validate(sql)
    except Exception as exc:  # noqa: BLE001 —— SQLSafetyError 等
        return {"ok": False, "error": f"SQL 被安全拦截: {exc}"}

    db_type = _safe_db_type()
    # 2) 真实执行计划（只读）。SQLite 用 EXPLAIN QUERY PLAN，更易读；其它用 EXPLAIN
    plan: Optional[Dict[str, Any]] = None
    plan_error: Optional[str] = None
    try:
        db = _lazy_db()
        prefix = "EXPLAIN QUERY PLAN" if db_type == "sqlite" else "EXPLAIN"
        df = db.query_readonly(f"{prefix} {stripped}")
        plan = {
            "columns": [str(c) for c in df.columns],
            "rows": _to_plain(df.head(_MAX_PLAN_ROWS).to_dict(orient="records")),
        }
    except Exception as exc:  # noqa: BLE001
        plan_error = str(exc)
        logger.warning("[explain_sql] EXPLAIN 失败（数据库可能不支持或语句复杂）: %s", plan_error)

    # 3) 规则式优化建议（不依赖 EXPLAIN，离线可用）
    advice = _lazy_optimizer().analyze(sql)

    logger.info("[explain_sql] 完成（plan=%s, suggestions=%d）", "ok" if plan else "skip", len(advice.get("suggestions", [])))
    return {
        "ok": True,
        "db_type": db_type,
        "plan": plan,
        "plan_note": plan_error or None,
        "suggestions": advice.get("suggestions", []),
        "indexes": advice.get("indexes", []),
        "optimized_sql": advice.get("optimized_sql"),
        "error": None if plan else plan_error,
    }


def _safe_db_type() -> str:
    """读取当前 DB 类型（不触发连接）。"""
    try:
        db = _lazy_db()
        return db.active_db_type
    except Exception:  # noqa: BLE001
        return os.getenv("DB_TYPE", "sqlite")


# ============================================================================
# MCP 注册（仅当 mcp SDK 可用）
# ============================================================================

if _HAS_MCP:
    mcp = _MCP_SERVER_CLASS(
        "text2sql",
        instructions=(
            "Text2SQL 数据查询助手：把自然语言转成 SQL 并对当前连接的数据库执行只读查询。\n"
            "工作流建议：先 get_schema 了解表结构 → 再 query_db 提问 或 explain_sql 检查某条 SQL 的执行计划。"
        ),
    )

    @mcp.tool(name="query_db")
    def query_db_tool(question: str) -> Dict[str, Any]:
        """自然语言查询数据库（Text-to-SQL）。入参 question 为中文自然语言问题，返回 SQL 与数据行。"""
        return query_db(question)

    @mcp.tool(name="get_schema")
    def get_schema_tool(table_name: Optional[str] = None) -> Dict[str, Any]:
        """查看数据库表结构。table_name 省略时返回全部表，给出列名/类型/主键。"""
        return get_schema(table_name)

    @mcp.tool(name="explain_sql")
    def explain_sql_tool(sql: str) -> Dict[str, Any]:
        """解释 SQL 的执行计划与优化建议。sql 必须是 SELECT/WITH 开头的只读查询。"""
        return explain_sql(sql)


def main() -> int:
    """启动 MCP stdio 服务（默认），或 --self-test 自检。"""
    if "--self-test" in sys.argv:
        return _self_test()

    if not _HAS_MCP:
        print(
            "未安装 MCP SDK。请先执行: pip install mcp\n"
            "然后通过 Claude Desktop 配置（见 README_MCP.md）调用。",
            file=sys.stderr,
        )
        return 1

    logger.info("启动 Text2SQL MCP Server（stdio）...")
    mcp.run(transport="stdio")
    return 0


def _self_test() -> int:
    """脱离 MCP 的逻辑自检：依次调用 3 个工具实现并打印 JSON。

    用法: python mcp_server.py --self-test
    （DB 连接、Key 均取自环境变量；不会改动任何核心代码）
    """
    print("== get_schema() ==")
    print(_dump(get_schema(None)))

    print("\n== explain_sql('SELECT 1') ==")
    print(_dump(explain_sql("SELECT 1 AS x")))

    print("\n== explain_sql('DELETE FROM t')（应被安全拦截） ==")
    print(_dump(explain_sql("DELETE FROM t")))

    q = os.getenv("MCP_SELFTEST_QUESTION", "统计当前库有多少张表？")
    print(f"\n== query_db('{q}')（需配置模型 Key 且 DB 可连） ==")
    print(_dump(query_db(q)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
