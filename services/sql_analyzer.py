"""
==============================================================================
SQL 语义分析模块 — 基于 sqlglot 解析，自动识别指标/图表类型
==============================================================================
设计思路：
  当用户将查询结果添加到大屏时，通过解析 SQL 语句的 SELECT 子句，
  自动判断该查询的结果应该展示为"指标卡"（单个数值）还是"图表"（多维度数据）。

  判断规则：
    1. 若所有 SELECT 列均为聚合函数（SUM/COUNT/AVG/MAX/MIN）且无 GROUP BY，
       则整体视为"指标"（metric）。
    2. 若有 GROUP BY 且同时存在聚合列和非聚合列，则非聚合列为维度、聚合列为指标，
       整体视为"图表"（chart）。
    3. 若 sqlglot 解析失败，降级为"结果形状"判断：一行一列视为指标，其他视为图表。

==============================================================================
"""

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("sql_analyzer")

# 聚合函数白名单 — 出现在此列表中的函数视为聚合操作
AGGREGATE_FUNCTIONS = {"SUM", "COUNT", "AVG", "MIN", "MAX", "GROUP_CONCAT", "DISTINCT"}


def _is_aggregate_expression(node: Any) -> bool:
    """
    递归判断表达式是否为聚合函数调用。

    参数:
        node: sqlglot AST 节点

    返回:
        True 如果该表达式是聚合函数调用
    """
    try:
        from sqlglot import exp as sqlglot_exp

        if isinstance(node, sqlglot_exp.AggFunc):
            return True
        if isinstance(node, sqlglot_exp.Count):
            return True
        if isinstance(node, sqlglot_exp.Sum):
            return True
        if isinstance(node, sqlglot_exp.Avg):
            return True
        if isinstance(node, sqlglot_exp.Min):
            return True
        if isinstance(node, sqlglot_exp.Max):
            return True
        if isinstance(node, sqlglot_exp.GroupConcat):
            return True
        # 检查函数名是否在白名单中
        if isinstance(node, sqlglot_exp.Func):
            func_name = node.sql_name().upper() if hasattr(node, 'sql_name') else (
                node.name.upper() if hasattr(node, 'name') else ""
            )
            if func_name in AGGREGATE_FUNCTIONS:
                return True
        # 处理别名校准
        if isinstance(node, sqlglot_exp.Alias):
            return _is_aggregate_expression(node.this)
        return False
    except ImportError:
        # sqlglot 不可用，无法判断
        return False
    except Exception as e:
        logger.debug(f"[SQL分析] _is_aggregate_expression 异常: {e}")
        return False


def _extract_select_expressions(sql: str) -> Tuple[Optional[Any], Optional[List[Any]], bool, bool]:
    """
    使用 sqlglot 解析 SQL，提取 SELECT 子句和 GROUP BY 信息。

    参数:
        sql: 原始 SQL 语句

    返回:
        (parsed, select_expressions, has_group_by, parse_ok)
    """
    try:
        import sqlglot
        from sqlglot import exp as sqlglot_exp

        parsed = sqlglot.parse_one(sql.strip().rstrip(";"))
        # 获取 SELECT 语句
        select = parsed.find(sqlglot_exp.Select)
        if select is None:
            # 尝试直接查询表达式
            if isinstance(parsed, sqlglot_exp.Select):
                select = parsed

        if select is None:
            return None, None, False, False

        # 提取 SELECT 子句中的表达式列表
        expressions = select.expressions if hasattr(select, 'expressions') else None
        if not expressions:
            return select, None, False, True

        # 判断是否有 GROUP BY
        group_by = select.args.get('group') if hasattr(select, 'args') else None
        has_group_by = group_by is not None

        return select, expressions, has_group_by, True

    except Exception as e:
        logger.debug(f"[SQL分析] sqlglot 解析失败: {e}")
        return None, None, False, False


def analyze_sql_semantics(sql: str) -> Dict[str, Any]:
    """
    分析 SQL 语义，自动识别结果类型为"指标"还是"图表"。

    参数:
        sql: 用户生成的 SQL 查询语句

    返回:
        {
            "type": "metric" | "chart",       # 识别结果
            "reason": str,                     # 识别依据说明
            "has_group_by": bool,              # 是否包含 GROUP BY
            "aggregate_count": int,            # 聚合列数量
            "total_columns": int,              # SELECT 总列数
            "parse_success": bool             # sqlglot 是否解析成功
        }
    """
    result = {
        "type": "chart",
        "reason": "",
        "has_group_by": False,
        "aggregate_count": 0,
        "total_columns": 0,
        "parse_success": False,
    }

    if not sql or not sql.strip():
        result["reason"] = "SQL 为空，默认视为图表"
        return result

    select, expressions, has_group_by, parse_ok = _extract_select_expressions(sql)

    if not parse_ok or select is None or expressions is None:
        # —— 降级策略：sqlglot 解析失败，无法确定 ——
        result["parse_success"] = False
        result["reason"] = "SQL 解析失败，请先用 SQL 查询获取结果后再添加"
        return result

    result["parse_success"] = True
    result["has_group_by"] = has_group_by
    result["total_columns"] = len(expressions)

    # 统计聚合列数量
    agg_count = sum(1 for expr in expressions if _is_aggregate_expression(expr))
    result["aggregate_count"] = agg_count

    if has_group_by:
        # —— 有 GROUP BY：视为图表（维度 + 聚合）——
        result["type"] = "chart"
        non_agg = result["total_columns"] - agg_count
        result["reason"] = (
            f"SQL 包含 GROUP BY 子句，{non_agg} 个维度列 + {agg_count} 个聚合列，"
            f"适合以图表展示"
        )
    elif agg_count > 0 and agg_count == result["total_columns"]:
        # —— 全部为聚合函数且无 GROUP BY：视为指标 ——
        result["type"] = "metric"
        result["reason"] = (
            f"SQL 全部为聚合函数（共 {agg_count} 列）且无 GROUP BY，"
            f"结果为汇总数值，适合以指标卡展示"
        )
    elif agg_count > 0 and agg_count < result["total_columns"]:
        # —— 混合列但无 GROUP BY：可能是带子查询的聚合，保守视为图表 ——
        result["type"] = "chart"
        non_agg = result["total_columns"] - agg_count
        result["reason"] = (
            f"SELECT 中包含 {non_agg} 个非聚合列和 {agg_count} 个聚合列，"
            f"适合以图表展示"
        )
    else:
        # —— 无聚合函数：纯数据查询，视为图表 ——
        result["type"] = "chart"
        result["reason"] = f"SELECT 中无聚合函数（共 {result['total_columns']} 列），适合以图表展示"

    return result


def analyze_result_shape(result_data: Optional[List[Dict[str, Any]]]) -> str:
    """
    基于结果形状的降级判断策略（当 SQL 解析不可用时使用）。

    参数:
        result_data: 查询结果数据（行列表）

    返回:
        "metric" 或 "chart"
    """
    if not result_data or len(result_data) == 0:
        return "chart"

    # 一行一列 → 指标
    if len(result_data) == 1 and len(result_data[0]) == 1:
        return "metric"

    # 其他 → 图表
    return "chart"


def combined_analysis(sql: str, result_data: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """
    综合判断：优先使用 SQL 语义分析，失败时降级为结果形状判断。

    参数:
        sql: SQL 查询语句
        result_data: 查询结果数据（可选，用于降级）

    返回:
        {
            "type": "metric" | "chart",
            "reason": str,
            "method": "sql_semantic" | "result_shape",  # 使用的判断方法
        }
    """
    # 优先 SQL 语义分析
    if sql and sql.strip():
        semantic = analyze_sql_semantics(sql)
        if semantic["parse_success"]:
            return {
                "type": semantic["type"],
                "reason": semantic["reason"],
                "method": "sql_semantic",
                "detail": semantic,
            }

    # 降级：结果形状判断
    shape_type = analyze_result_shape(result_data)
    shape_reason = {
        "metric": "SQL 解析失败，结果为单行单列，按指标展示",
        "chart": "SQL 解析失败，结果为多行或多列，按图表展示",
    }
    return {
        "type": shape_type,
        "reason": shape_reason.get(shape_type, "使用结果形状判断"),
        "method": "result_shape",
    }
