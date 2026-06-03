"""
==============================================================================
SQL 验证模块 — sqlglot 语法校验 + 表/字段存在性检查 + 错误恢复链
==============================================================================
设计思路：
  Text-to-SQL 场景下，LLM 生成的 SQL 可能包含语法错误或引用了不存在的表/字段。
  本模块在 SQL 执行前进行多层校验，尽早发现并分类错误。

  三层校验：
    1. sqlglot 语法解析 → 检查 SQL 是否符合 SQLite 语法
    2. 表存在性检查 → 确保引用的表在数据库中真实存在
    3. 字段存在性检查 → 确保引用的字段属于对应表

  错误恢复链：
    对常见错误类型进行分类，提供针对性的修正策略。
==============================================================================
"""

import logging
import re
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field

from config import CONFIG

logger = logging.getLogger("sql_validator")


# ============================================================================
# 错误分类
# ============================================================================

@dataclass
class SQLError:
    """SQL 错误信息"""
    error_type: str          # 错误类型
    message: str             # 错误描述
    recovery_hint: str       # 修正建议
    position: Optional[int] = None  # 错误位置（如有）


class ErrorCategory:
    """错误类型分类常量"""
    SYNTAX_ERROR = "语法错误"
    TABLE_NOT_FOUND = "表不存在"
    COLUMN_NOT_FOUND = "字段不存在"
    FUNCTION_ERROR = "函数错误"
    JOIN_ERROR = "JOIN 关联错误"
    GROUP_BY_ERROR = "GROUP BY 错误"
    TYPE_MISMATCH = "类型不匹配"
    UNKNOWN_ERROR = "未知错误"


# ============================================================================
# Schema 缓存（用于表/字段存在性检查）
# ============================================================================

class SchemaInspector:
    """
    数据库 Schema 检查器。

    缓存数据库中所有表名和字段名，用于验证 SQL 中引用的
    表和字段是否真实存在。
    """

    def __init__(self):
        self._table_names: List[str] = []
        self._column_map: Dict[str, List[str]] = {}  # table_name -> [column_names]
        self._column_type_map: Dict[str, Dict[str, str]] = {}  # table_name -> {col_name: col_type}
        self._loaded = False

    def load(self):
        """从数据库加载 Schema 信息"""
        if self._loaded:
            return
        try:
            from database import get_db
            db = get_db()
            tables_info = db.get_table_info()
            for info in tables_info:
                tbl = info["table_name"]
                self._table_names.append(tbl)
                self._column_map[tbl] = []
                self._column_type_map[tbl] = {}
                for col in info["columns"]:
                    self._column_map[tbl].append(col["name"])
                    self._column_type_map[tbl][col["name"]] = col["type"]
            self._loaded = True
            logger.info(f"[SchemaInspector] 已加载 {len(self._table_names)} 张表")
        except Exception as e:
            logger.warning(f"[SchemaInspector] 加载失败: {e}")

    def get_table_names(self) -> List[str]:
        self.load()
        return self._table_names

    def get_columns(self, table_name: str) -> List[str]:
        self.load()
        return self._column_map.get(table_name, [])

    def get_column_type(self, table_name: str, column_name: str) -> Optional[str]:
        self.load()
        return self._column_type_map.get(table_name, {}).get(column_name)

    def has_table(self, table_name: str) -> bool:
        self.load()
        return table_name in self._table_names

    def has_column(self, table_name: str, column_name: str) -> bool:
        self.load()
        return column_name in self._column_map.get(table_name, [])


# ============================================================================
# sqlglot SQL 验证器
# ============================================================================

class SQLSyntaxValidator:
    """
    基于 sqlglot 的 SQL 语法验证器。

    功能：
    1. 解析 SQL 并检查语法正确性
    2. 提取 SQL 中引用的表名和字段名
    3. 验证表和字段的存在性
    """

    def __init__(self, dialect: str = "sqlite"):
        self._sqlglot = None
        self._initialized = False
        self._dialect = dialect

    def _init_sqlglot(self):
        """延迟加载 sqlglot"""
        if not self._initialized:
            try:
                import sqlglot as _sg
                self._sqlglot = _sg
                self._initialized = True
            except ImportError:
                logger.warning("[SQL语法] sqlglot 未安装，降级为基础校验")
                self._initialized = False

    def validate_syntax(self, sql: str) -> List[SQLError]:
        """
        使用 sqlglot 解析 SQL 语法。

        返回:
            错误列表（空列表表示语法正确）
        """
        errors = []
        self._init_sqlglot()

        # 清理 SQL
        sql_clean = sql.strip().rstrip(";")

        if self._initialized and self._sqlglot is not None:
            # 使用 sqlglot 进行完整语法解析
            try:
                parsed = self._sqlglot.parse_one(sql_clean, dialect=self._dialect)
                # 如果能解析成功，基本语法正确
                # 检查是否为 SELECT 语句
                if parsed is None:
                    errors.append(SQLError(
                        error_type=ErrorCategory.SYNTAX_ERROR,
                        message="无法解析 SQL 语句",
                        recovery_hint="请检查 SQL 关键字拼写是否正确",
                    ))
                else:
                    # 验证顶层语句类型
                    stmt_type = type(parsed).__name__
                    if stmt_type == "Command":
                        errors.append(SQLError(
                            error_type=ErrorCategory.SYNTAX_ERROR,
                            message=f"不支持的语句类型: {parsed.name}",
                            recovery_hint="只支持 SELECT 查询语句",
                        ))
            except Exception as e:
                error_msg = str(e)
                # 提取错误位置
                pos_match = re.search(r'(\d+)', error_msg)
                position = int(pos_match.group(1)) if pos_match else None

                # 分类常见错误
                error_type = self._classify_syntax_error(error_msg)
                errors.append(SQLError(
                    error_type=error_type,
                    message=error_msg,
                    recovery_hint=self._get_recovery_hint(error_type, error_msg),
                    position=position,
                ))
        else:
            # sqlglot 不可用，使用正则基础校验
            errors = self._basic_syntax_check(sql_clean)

        return errors

    def _classify_syntax_error(self, error_msg: str) -> str:
        """将 sqlglot 错误信息归类"""
        error_lower = error_msg.lower()

        if "table" in error_lower and "not found" in error_lower:
            return ErrorCategory.TABLE_NOT_FOUND
        if "column" in error_lower and "not found" in error_lower:
            return ErrorCategory.COLUMN_NOT_FOUND
        if "function" in error_lower:
            return ErrorCategory.FUNCTION_ERROR
        if "join" in error_lower:
            return ErrorCategory.JOIN_ERROR
        if "group by" in error_lower:
            return ErrorCategory.GROUP_BY_ERROR
        if "type" in error_lower:
            return ErrorCategory.TYPE_MISMATCH
        if "expected" in error_lower or "unexpected" in error_lower:
            return ErrorCategory.SYNTAX_ERROR
        return ErrorCategory.UNKNOWN_ERROR

    def _get_recovery_hint(self, error_type: str, error_msg: str) -> str:
        """根据错误类型给出修正建议"""
        hints = {
            ErrorCategory.SYNTAX_ERROR: "检查 SQL 关键字拼写、括号匹配和逗号位置",
            ErrorCategory.TABLE_NOT_FOUND: "检查表名拼写，确认使用了正确的表名（users, products, orders）",
            ErrorCategory.COLUMN_NOT_FOUND: "检查字段名拼写，确认字段属于对应的表",
            ErrorCategory.FUNCTION_ERROR: "SQLite 不支持此函数，尝试使用 SQLite 内置函数替代",
            ErrorCategory.JOIN_ERROR: "检查 JOIN 条件中的字段名和表名是否正确",
            ErrorCategory.GROUP_BY_ERROR: "SELECT 中的非聚合字段必须包含在 GROUP BY 中",
            ErrorCategory.TYPE_MISMATCH: "检查字段类型是否匹配（如字符串需要引号）",
        }
        return hints.get(error_type, "请仔细检查 SQL 语句")

    def _basic_syntax_check(self, sql: str) -> List[SQLError]:
        """基础正则语法检查（sqlglot 不可用时的降级方案）"""
        errors = []

        # 检查括号匹配
        open_parens = sql.count("(")
        close_parens = sql.count(")")
        if open_parens != close_parens:
            errors.append(SQLError(
                error_type=ErrorCategory.SYNTAX_ERROR,
                message=f"括号不匹配: 左括号 {open_parens} 个，右括号 {close_parens} 个",
                recovery_hint="检查括号是否成对出现",
            ))

        # 检查基本关键字
        sql_upper = sql.upper()
        if not sql_upper.startswith("SELECT"):
            errors.append(SQLError(
                error_type=ErrorCategory.SYNTAX_ERROR,
                message="SQL 必须以 SELECT 开头",
                recovery_hint="只支持 SELECT 查询语句",
            ))

        # 检查 FROM
        if "FROM" not in sql_upper:
            errors.append(SQLError(
                error_type=ErrorCategory.SYNTAX_ERROR,
                message="缺少 FROM 子句",
                recovery_hint="SELECT 语句需要 FROM 子句指定查询表",
            ))

        return errors

    def extract_tables(self, sql: str) -> List[str]:
        """
        从 SQL 中提取引用的表名。

        使用 sqlglot 解析器提取，如不可用则使用正则。
        """
        self._init_sqlglot()

        if self._initialized and self._sqlglot is not None:
            try:
                parsed = self._sqlglot.parse_one(sql.strip().rstrip(";"), dialect=self._dialect)
                tables = []
                for table in parsed.find_all(self._sqlglot.expressions.Table):
                    if table.name:
                        tables.append(table.name)
                return list(set(tables))
            except Exception:
                pass

        # 正则回退：提取 FROM/JOIN 后面的表名
        return list(set(re.findall(
            r'(?:FROM|JOIN|UPDATE|INTO)\s+(\w+)',
            sql, re.IGNORECASE
        )))


# ============================================================================
# 表/字段存在性验证器
# ============================================================================

class TableColumnValidator:
    """
    验证 SQL 中引用的表名和字段名在数据库中是否存在。
    """

    def __init__(self, inspector: Optional[SchemaInspector] = None):
        self.inspector = inspector or SchemaInspector()

    def validate_tables(self, sql: str, syntax_validator: SQLSyntaxValidator) -> List[SQLError]:
        """检查 SQL 中引用的所有表是否存在"""
        errors = []
        tables = syntax_validator.extract_tables(sql)

        for tbl in tables:
            if not self.inspector.has_table(tbl):
                errors.append(SQLError(
                    error_type=ErrorCategory.TABLE_NOT_FOUND,
                    message=f"表 '{tbl}' 不存在",
                    recovery_hint=f"可用表: {', '.join(self.inspector.get_table_names())}",
                ))

        return errors

    def validate_columns(self, sql: str) -> List[SQLError]:
        """
        对 SQL 中的字段引用进行存在性检查。

        使用正则提取 `table.column` 和 `alias.column` 模式，
        验证字段是否存在于对应表中。
        """
        errors = []
        tables = self.inspector.get_table_names()

        # 提取 table.column 模式
        qualified_refs = re.findall(r'(\w+)\.(\w+)', sql)
        for tbl, col in qualified_refs:
            # 跳过函数调用（如 strftime, date 等）
            if col.upper() in ("COUNT", "SUM", "AVG", "MIN", "MAX", "COALESCE", "IFNULL",
                               "ROUND", "STRFTIME", "DATE", "DATETIME", "UPPER", "LOWER",
                               "LENGTH", "SUBSTR", "TRIM", "REPLACE", "ABS", "TYPEOF",
                               "INSTR", "LIKE", "BETWEEN", "IN", "NOT", "AND", "OR", "AS",
                               "ON", "DISTINCT", "CASE", "WHEN", "THEN", "ELSE", "END",
                               "NULL", "IS", "ORDER", "GROUP", "HAVING", "LIMIT", "OFFSET",
                               "LEFT", "RIGHT", "INNER", "OUTER", "CROSS", "FULL", "JOIN",
                               "DESC", "ASC", "BY"):
                continue
            # 检查表名是否有效
            if tbl in tables:
                if not self.inspector.has_column(tbl, col):
                    valid_cols = self.inspector.get_columns(tbl)
                    errors.append(SQLError(
                        error_type=ErrorCategory.COLUMN_NOT_FOUND,
                        message=f"表 '{tbl}' 中不存在字段 '{col}'",
                        recovery_hint=f"表 '{tbl}' 的可用字段: {', '.join(valid_cols)}",
                    ))

        return errors


# ============================================================================
# 安全校验器（防注入 + 只读检查）
# ============================================================================

class SecurityValidator:
    """
    SQL 安全校验器。

    检查 SQL 是否只包含 SELECT 查询，不包含危险操作。
    增强版，支持更细粒度的控制。
    """

    # 禁止出现的 SQL 关键字模式
    DANGEROUS_PATTERNS = [
        (r'\bINSERT\b', "不允许 INSERT 操作"),
        (r'\bUPDATE\b', "不允许 UPDATE 操作"),
        (r'\bDELETE\b', "不允许 DELETE 操作"),
        (r'\bDROP\b', "不允许 DROP 操作"),
        (r'\bALTER\b', "不允许 ALTER 操作"),
        (r'\bCREATE\b', "不允许 CREATE 操作"),
        (r'\bTRUNCATE\b', "不允许 TRUNCATE 操作"),
        (r'\bEXEC\b', "不允许 EXEC 操作"),
        (r'\bEXECUTE\b', "不允许 EXECUTE 操作"),
        (r'\bATTACH\b', "不允许 ATTACH 操作"),
        (r'\bDETACH\b', "不允许 DETACH 操作"),
        (r'\bREINDEX\b', "不允许 REINDEX 操作"),
        (r'\bREPLACE\b', "不允许 REPLACE 操作（除非是函数名）"),
    ]

    @classmethod
    def validate(cls, sql: str) -> Tuple[bool, str]:
        """
        校验 SQL 安全性。

        返回:
            (is_valid: bool, error_message: str)
        """
        sql_upper = sql.strip().upper()

        # 检查是否以 SELECT 或 WITH 开头
        if not (sql_upper.startswith("SELECT") or sql_upper.startswith("WITH")):
            return False, "只允许 SELECT 查询语句（或 WITH 子句）"

        # 检查是否包含危险操作
        for pattern, msg in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, sql_upper):
                return False, f"SQL 包含禁止的操作: {msg}"

        # 检查是否包含注释符
        if "--" in sql or "/*" in sql:
            return False, "SQL 不允许包含注释"

        return True, ""


# ============================================================================
# 错误恢复链
# ============================================================================

class ErrorRecoveryChain:
    """
    SQL 错误恢复链。

    根据不同类型的错误，提供针对性的修正策略。
    用于 Agent 自我修正时生成更有针对性的 Prompt。
    """

    # 各错误类型的修正策略模板
    RECOVERY_STRATEGIES = {
        ErrorCategory.SYNTAX_ERROR: (
            "修正 SQL 语法错误：\n"
            "1. 检查关键字拼写是否正确\n"
            "2. 检查括号是否成对匹配\n"
            "3. 检查逗号位置是否正确\n"
            "4. 确保字符串使用了正确的引号"
        ),
        ErrorCategory.TABLE_NOT_FOUND: (
            "修正表名引用错误：\n"
            "1. 确认表名拼写完全正确\n"
            "2. 数据库中的可用表: users, products, orders\n"
            "3. 检查是否有表别名冲突"
        ),
        ErrorCategory.COLUMN_NOT_FOUND: (
            "修正字段名引用错误：\n"
            "1. 确认字段名拼写完全正确\n"
            "2. 检查字段所属的表是否正确\n"
            "3. 使用 `表名.字段名` 格式消除歧义"
        ),
        ErrorCategory.FUNCTION_ERROR: (
            "修正函数错误：\n"
            "1. 确认使用的函数在 SQLite 中支持\n"
            "2. SQLite 常用函数: COUNT, SUM, AVG, MIN, MAX, ROUND,"
            " STRFTIME, DATE, COALESCE, IFNULL\n"
            "3. 检查函数参数个数和类型"
        ),
        ErrorCategory.JOIN_ERROR: (
            "修正 JOIN 关联错误：\n"
            "1. 确认 JOIN 条件中的字段存在于对应表中\n"
            "2. 确认 ON 条件使用了正确的关联字段\n"
            "3. 考虑使用 LEFT JOIN 替代 INNER JOIN"
        ),
        ErrorCategory.GROUP_BY_ERROR: (
            "修正 GROUP BY 错误：\n"
            "1. SELECT 中的非聚合字段必须全部出现在 GROUP BY 中\n"
            "2. 或使用聚合函数包裹非分组字段\n"
            "3. GROUP BY 中可以使用字段位置序号"
        ),
        ErrorCategory.TYPE_MISMATCH: (
            "修正类型不匹配错误：\n"
            "1. 字符串值需要使用单引号包裹\n"
            "2. 数字不需要引号\n"
            "3. 日期比较使用正确的格式"
        ),
    }

    @classmethod
    def get_recovery_strategy(cls, error_type: str, error_message: str) -> str:
        """
        根据错误类型获取修正策略文本。

        用于注入到 Agent 的 SQL_CORRECTION_PROMPT_TEMPLATE 中。
        """
        strategy = cls.RECOVERY_STRATEGIES.get(
            error_type,
            "分析错误原因并修正 SQL：\n"
            "1. 仔细阅读错误信息\n"
            "2. 对比数据库表结构确认引用正确\n"
            "3. 确保 SQL 语法兼容 SQLite"
        )

        return (
            f"【错误类型】{error_type}\n"
            f"【错误详情】{error_message}\n"
            f"【修正策略】\n{strategy}"
        )


# ============================================================================
# 统一验证入口
# ============================================================================

class SQLValidator:
    """
    统一的 SQL 验证器。

    整合语法校验、表/字段存在性检查、安全校验三层验证。
    提供详细的错误分类和修正建议。
    """

    def __init__(self, dialect: str = "sqlite"):
        self.syntax_validator = SQLSyntaxValidator(dialect=dialect)
        self.table_column_validator = TableColumnValidator()
        self.security_validator = SecurityValidator()
        self.error_recovery = ErrorRecoveryChain()

    def validate_all(self, sql: str) -> Tuple[bool, List[SQLError]]:
        """
        执行全部三层验证。

        返回:
            (is_valid: bool, errors: List[SQLError])
        """
        all_errors: List[SQLError] = []

        # Layer 1: 安全检查（最轻量，最先执行）
        safe, sec_msg = self.security_validator.validate(sql)
        if not safe:
            all_errors.append(SQLError(
                error_type="安全违规",
                message=sec_msg,
                recovery_hint="只允许 SELECT 查询语句",
            ))
            return False, all_errors

        # Layer 2: 语法检查（sqlglot）
        syntax_errors = self.syntax_validator.validate_syntax(sql)
        if syntax_errors:
            all_errors.extend(syntax_errors)
            return False, all_errors

        # Layer 3: 表/字段存在性检查
        table_errors = self.table_column_validator.validate_tables(
            sql, self.syntax_validator
        )
        all_errors.extend(table_errors)

        column_errors = self.table_column_validator.validate_columns(sql)
        all_errors.extend(column_errors)

        return len(all_errors) == 0, all_errors

    def format_errors_for_prompt(self, errors: List[SQLError]) -> str:
        """
        将错误列表格式化为给 LLM 的修正指令。

        用于注入 Agent 的修正 Prompt。
        """
        if not errors:
            return ""

        parts = ["【SQL 验证失败，请修正以下错误】"]
        for i, err in enumerate(errors, 1):
            parts.append(f"\n错误 {i}: [{err.error_type}] {err.message}")
            if err.recovery_hint:
                parts.append(f"  建议: {err.recovery_hint}")

        # 附加通用修正策略
        parts.append("\n\n【通用修正指引】")
        parts.append("1. 仔细检查表名和字段名拼写")
        parts.append("2. 确认 SELECT 和 GROUP BY 字段的一致性")
        parts.append("3. 确保 JOIN 条件使用了正确的关联键")
        parts.append("4. 使用 SQLite 兼容的语法和函数")

        return "\n".join(parts)

    @classmethod
    def validate(cls, sql: str) -> Tuple[bool, str]:
        """
        简洁的校验接口（兼容旧版）。

        返回:
            (is_valid: bool, error_message: str)
        """
        return SecurityValidator.validate(sql)


# ============================================================================
# 便捷函数
# ============================================================================

def get_validator() -> SQLValidator:
    """获取验证器实例"""
    return SQLValidator()


def validate_sql(sql: str) -> Tuple[bool, str]:
    """
    一键 SQL 验证。

    参数:
        sql: SQL 语句

    返回:
        (是否通过, 错误信息或空字符串)
    """
    validator = SQLValidator()
    is_valid, errors = validator.validate_all(sql)
    if is_valid:
        return True, ""
    return False, validator.format_errors_for_prompt(errors)


# ============================================================================
# 独立测试入口
# ============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    validator = SQLValidator()
    test_sqls = [
        "SELECT SUM(total_amount) FROM orders WHERE order_date > '2024-01-01'",
        "SELECT u.username, SUM(o.total_amount) FROM orders o JOIN users u ON o.user_id = u.user_id GROUP BY u.username",
        "SELECT * FROM non_existent_table",
        "DROP TABLE users",
        "SELECT u.name FROM users u",
        "SELECT invalid_function(x) FROM orders",
    ]

    for sql in test_sqls:
        print(f"\n{'='*60}")
        print(f"SQL: {sql}")
        is_valid, result = validator.validate_all(sql)
        if is_valid:
            print("  ✓ 验证通过")
        else:
            for err in result:
                print(f"  ✗ [{err.error_type}] {err.message}")
                print(f"    建议: {err.recovery_hint}")
