"""
===============================================================================
SQL 优化器 — 查询性能分析、索引建议、执行计划解读
===============================================================================
参考来源:
  - antigravity-awesome-skills/skills/sql-optimization-patterns
  - anthropic/skills/sql-optimization-patterns
  - NL2SQL (performance_validator.py)

集成方式:
  from sql_optimizer import optimize_sql, explain_query, suggest_indexes

  在 agent.py 中可通过配置启用:
    self.sql_optimizer = SQLOptimizer(enabled=CONFIG.agent.enable_sql_optimizer)
===============================================================================
"""

import re
from typing import List, Dict, Optional, Tuple


# ============================================================================
# SQL 优化规则库
# ============================================================================

OPTIMIZATION_RULES = [
    {
        "id": "SELECT_STAR",
        "pattern": r'\bSELECT\s+\*',
        "message": "避免 SELECT *，只查询需要的字段以减少 IO",
        "severity": "warning",
        "fix": lambda sql: sql,  # 无法自动修复
    },
    {
        "id": "MISSING_WHERE",
        "pattern": r'\bSELECT\s+.*\bFROM\s+\w+\s*(?:LIMIT|$|JOIN)',
        "message": "查询缺少 WHERE 条件，将进行全表扫描",
        "severity": "critical",
    },
    {
        "id": "LIKE_LEADING_WILDCARD",
        "pattern": r"LIKE\s+'%",
        "message": "LIKE 以 % 开头无法使用索引，考虑全文检索或反向like",
        "severity": "warning",
    },
    {
        "id": "OR_WITHOUT_INDEX",
        "pattern": r'\bOR\b',
        "message": "OR 条件可能无法使用联合索引，考虑用 UNION ALL 替代",
        "severity": "info",
    },
    {
        "id": "NEGATION_IN_WHERE",
        "pattern": r'\bWHERE\s+.*\b(?:<>|!=|NOT\s+IN|NOT\s+LIKE|NOT\s+BETWEEN)',
        "message": "否定条件无法使用索引，考虑改写为正向条件",
        "severity": "warning",
    },
    {
        "id": "FUNCTION_ON_COLUMN",
        "pattern": r'\bWHERE\s+\w+\s*\([^)]+\)',
        "message": "WHERE 中对字段使用函数会阻止索引使用，考虑使用函数索引",
        "severity": "warning",
    },
    {
        "id": "IMPLICIT_TYPE_CAST",
        "pattern": r"(?:WHERE|ON|AND)\s+\w+\s*=\s*'(\d+)'",
        "message": "数字与字符串比较可能导致隐式类型转换，无法使用索引",
        "severity": "info",
    },
    {
        "id": "NO_LIMIT",
        "pattern": r'\bSELECT\b(?!.*\bLIMIT\b)',
        "message": "查询没有 LIMIT 限制，大数据量时可能导致 OOM",
        "severity": "warning",
        "exclude_if": r'\bCOUNT\b|\bEXISTS\b',
    },
    {
        "id": "DISTINCT_JOIN",
        "pattern": r'\bDISTINCT\b.*\bJOIN\b',
        "message": "DISTINCT + JOIN 通常表示通过 JOIN 产生了重复行，考虑用 EXISTS 替代",
        "severity": "info",
    },
    {
        "id": "ORDER_BY_RAND",
        "pattern": r'\bORDER\s+BY\s+RAND\b',
        "message": "ORDER BY RAND() 需要全表扫描，考虑用其他随机方案",
        "severity": "critical",
    },
    {
        "id": "IN_SUBQUERY",
        "pattern": r'\bIN\s*\(SELECT\b',
        "message": "IN (SELECT ...) 可能性能较差，考虑用 EXISTS 或 JOIN 替代",
        "severity": "info",
    },
    {
        "id": "OFFSET_LARGE",
        "pattern": r'\bLIMIT\s+\d+\s*,\s*\d+|\bOFFSET\b',
        "message": "OFFSET/LIMIT 大偏移量会导致扫描大量行，考虑用游标分页替代",
        "severity": "warning",
    },
    {
        "id": "GROUP_BY_NONINDEXED",
        "pattern": r'\bGROUP\s+BY\s+\w+',
        "message": "GROUP BY 确保分组字段有索引，否则会使用 filesort",
        "severity": "info",
    },
    {
        "id": "SELECT_IN_SELECT",
        "pattern": r'\(SELECT\s',
        "message": "子查询在 SELECT 中会每行执行一次，考虑用 JOIN 或窗口函数替代",
        "severity": "warning",
    },
]


# ============================================================================
# 索引建议规则
# ============================================================================

INDEX_SUGGESTIONS = [
    {
        "pattern": r'\bWHERE\s+(\w+)\s*[=<>]',
        "desc": "WHERE 条件字段",
        "suggestion": "考虑为 {field} 添加索引",
    },
    {
        "pattern": r'\bJOIN\s+\w+\s+\w+\s+ON\s+\w+\.(\w+)\s*=',
        "desc": "JOIN 关联字段",
        "suggestion": "JOIN 字段 {field} 应在关联表上有索引",
    },
    {
        "pattern": r'\bORDER\s+BY\s+(\w+)',
        "desc": "排序字段",
        "suggestion": "ORDER BY {field} 建议加索引以避免 filesort",
    },
    {
        "pattern": r'\bGROUP\s+BY\s+(\w+)',
        "desc": "分组字段",
        "suggestion": "GROUP BY {field} 建议复合索引",
    },
]


# ============================================================================
# 主优化器类
# ============================================================================

class SQLOptimizer:
    """
    SQL 查询优化器 — 分析 SQL 并给出优化建议。

    用法:
        optimizer = SQLOptimizer()
        result = optimizer.analyze("SELECT * FROM orders WHERE name LIKE '%abc%'")
        print(result["suggestions"])
        print(result["indexes"])
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def analyze(self, sql: str) -> Dict:
        """
        分析 SQL 查询，返回优化建议和索引建议。

        返回:
        {
            "suggestions": [{"id": str, "message": str, "severity": str}, ...],
            "indexes": [{"field": str, "suggestion": str}, ...],
            "optimized_sql": str | None,  # 如果能自动优化则返回
        }
        """
        if not self.enabled or not sql:
            return {"suggestions": [], "indexes": [], "optimized_sql": None}

        suggestions = self._check_rules(sql)
        indexes = self._suggest_indexes(sql)
        optimized = self._auto_fix(sql)

        return {
            "suggestions": suggestions,
            "indexes": indexes,
            "optimized_sql": optimized,
        }

    def _check_rules(self, sql: str) -> List[Dict]:
        """检查所有优化规则"""
        results = []
        sql_upper = sql.upper().strip()

        for rule in OPTIMIZATION_RULES:
            # 检查排除条件
            if "exclude_if" in rule and re.search(rule["exclude_if"], sql_upper, re.IGNORECASE):
                continue
            if re.search(rule["pattern"], sql_upper, re.IGNORECASE):
                results.append({
                    "id": rule["id"],
                    "message": rule["message"],
                    "severity": rule.get("severity", "info"),
                })

        return results

    def _suggest_indexes(self, sql: str) -> List[Dict]:
        """从 SQL 中提取需要索引的字段"""
        suggestions = []
        for rule in INDEX_SUGGESTIONS:
            matches = re.finditer(rule["pattern"], sql, re.IGNORECASE)
            for m in matches:
                field = m.group(1)
                if field.upper() not in ("TRUE", "FALSE", "NULL", "AND", "OR", "NOT"):
                    suggestions.append({
                        "field": field,
                        "suggestion": rule["suggestion"].format(field=field),
                    })
        return suggestions

    def _auto_fix(self, sql: str) -> Optional[str]:
        """自动修复常见问题（能安全修复的）"""
        fixed = sql

        # 修复 SELECT * → 提示但不自动改（不确定需要哪些字段）
        # 添加 LIMIT（如果没有且不是聚合查询）
        sql_upper = fixed.upper().strip()
        if not re.search(r'\bLIMIT\b', sql_upper) and not re.search(r'\bCOUNT\b', sql_upper):
            if re.match(r'SELECT\s', sql_upper) and not sql_upper.startswith("SELECT COUNT"):
                fixed = fixed.rstrip(';') + " LIMIT 100"
                return fixed

        return None


# ============================================================================
# 便捷函数
# ============================================================================

_optimizer: Optional[SQLOptimizer] = None


def get_optimizer() -> SQLOptimizer:
    """获取优化器单例"""
    global _optimizer
    if _optimizer is None:
        _optimizer = SQLOptimizer()
    return _optimizer


def optimize_sql(sql: str) -> Dict:
    """快捷分析 SQL"""
    return get_optimizer().analyze(sql)


def suggest_indexes(sql: str) -> List[Dict]:
    """快捷获取索引建议"""
    return get_optimizer()._suggest_indexes(sql)
