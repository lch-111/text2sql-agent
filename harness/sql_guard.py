"""
==============================================================================
SQLGuard — 物理安全拦截器（不可绕过）
==============================================================================
设计思路：
  SQLGuard 在 SQL 执行前做物理级别的安全拦截。
  它独立于业务逻辑，系统启动时加载，执行路径上不可跳过。

  拦截规则完全遵循 .harness/rules/sql-safety-rules.md 中的黑名单：
  - DDL 语句（结构修改）：CREATE TABLE, ALTER TABLE, DROP TABLE 等
  - DML 写操作：INSERT, UPDATE, DELETE, MERGE 等
  - 权限与系统操作：GRANT, REVOKE, EXECUTE, CALL 等

  拦截日志单独记录到 logs/audit/ 目录，用于安全审计。
==============================================================================
"""

import logging
import os
import re
from datetime import datetime

logger = logging.getLogger("sql_guard")

# 审计日志目录
_AUDIT_LOG_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "logs", "audit"
)


class SQLSafetyError(Exception):
    """
    SQL 安全拦截异常。

    当 SQL 语句匹配黑名单规则时抛出此异常，
    上层代码捕获后返回安全提示给用户。
    """

    def __init__(self, message: str, sql: str = "", rule: str = ""):
        self.message = message
        self.sql = sql
        self.rule = rule
        super().__init__(self.message)


class SQLGuard:
    """
    SQL 物理安全拦截器。

    在 SQL 执行前调用 validate() 方法，
    匹配黑名单规则则抛出 SQLSafetyError。

    用法:
        guard = SQLGuard()
        try:
            guard.validate("SELECT * FROM orders")
            # 继续执行
        except SQLSafetyError as e:
            # 返回安全提示
            pass
    """

    # ========================================================================
    # 黑名单规则（从 .harness/rules/sql-safety-rules.md 精确实现）
    # ========================================================================

    # DDL 语句 — 结构修改
    DDL_PATTERNS = [
        r"\bCREATE\s+TABLE\b",
        r"\bALTER\s+TABLE\b",
        r"\bDROP\s+TABLE\b",
        r"\bTRUNCATE\s+TABLE\b",
        r"\bCREATE\s+INDEX\b",
        r"\bDROP\s+INDEX\b",
        r"\bCREATE\s+VIEW\b",
        r"\bDROP\s+VIEW\b",
        r"\bCREATE\s+SCHEMA\b",
        r"\bDROP\s+SCHEMA\b",
        r"\bCREATE\s+DATABASE\b",
        r"\bDROP\s+DATABASE\b",
    ]

    # DML 写操作
    DML_WRITE_PATTERNS = [
        r"\bINSERT\s+INTO\b",
        r"\bUPDATE\b",
        r"\bDELETE\s+FROM\b",
        r"\bMERGE\s+INTO\b",
        r"\bREPLACE\s+INTO\b",
        r"\bUPSERT\b",
    ]

    # 权限与系统操作
    PRIVILEGE_PATTERNS = [
        r"\bGRANT\b",
        r"\bREVOKE\b",
        r"\bSET\s+PASSWORD\b",
        r"\bEXECUTE\b",
        r"\bCALL\b",
        r"\bLOAD\s+DATA\s+INFILE\b",
        r"\bEXEC\b",
        r"\bSP_EXECUTESQL\b",
        r"\bXP_CMDSHELL\b",
    ]

    # 所有黑名单模式合并
    BLACKLIST = DDL_PATTERNS + DML_WRITE_PATTERNS + PRIVILEGE_PATTERNS

    def __init__(self, enabled: bool = True):
        """
        初始化 SQLGuard。

        参数:
            enabled: 是否启用安全拦截（生产环境必须为 True）
        """
        self.enabled = enabled
        if enabled:
            logger.info(
                f"[SQLGuard] 已启用，{len(self.BLACKLIST)} 条黑名单规则"
            )
        else:
            logger.warning("[SQLGuard] 安全拦截已禁用！（仅用于开发调试）")

    def validate(self, sql: str) -> bool:
        """
        校验 SQL 语句是否安全。

        遍历黑名单规则进行匹配（不区分大小写），
        匹配到任何规则则抛出 SQLSafetyError 并记录审计日志。

        参数:
            sql: 待校验的 SQL 语句

        返回:
            True — SQL 安全，可以执行

        异常:
            SQLSafetyError — SQL 包含危险操作，已被拦截
        """
        if not self.enabled:
            return True

        if not sql or not sql.strip():
            raise SQLSafetyError(
                message="SQL 语句为空",
                sql=sql,
                rule="EMPTY_SQL",
            )

        # 先检查是否以 SELECT/WITH 开头（严格模式）
        sql_stripped = sql.strip()
        sql_upper = sql_stripped.upper()

        # 允许 WITH 和 SELECT 开头
        if not re.match(r"^\s*(SELECT|WITH)\b", sql_upper):
            # 检查注释中可能隐藏的危险操作
            clean_sql = re.sub(r"--.*$", "", sql_upper, flags=re.MULTILINE)
            clean_sql = re.sub(r"/\*.*?\*/", "", clean_sql, flags=re.DOTALL)
            if not re.match(r"^\s*(SELECT|WITH)\b", clean_sql):
                raise SQLSafetyError(
                    message="只允许 SELECT 查询语句",
                    sql=sql_stripped,
                    rule="ONLY_SELECT_ALLOWED",
                )

        # 逐条匹配黑名单（在清理注释后的 SQL 上匹配）
        clean_sql = re.sub(r"--.*$", "", sql_upper, flags=re.MULTILINE)
        clean_sql = re.sub(r"/\*.*?\*/", "", clean_sql, flags=re.DOTALL)

        for pattern in self.BLACKLIST:
            if re.search(pattern, clean_sql):
                # 忽略 SELECT 语句中的合法子查询（如 SELECT COUNT(*) 中的 INSERT 误匹配）
                # 但严格模式不做例外处理 —— 安全优先
                matched_rule = pattern
                self._log_audit(sql_stripped, pattern)
                logger.critical(
                    f"[SQLGuard] 危险 SQL 被拦截: {sql_stripped[:150]}"
                )
                raise SQLSafetyError(
                    message=f"禁止执行危险操作：匹配规则 {pattern}",
                    sql=sql_stripped,
                    rule=pattern,
                )

        return True

    def _log_audit(self, sql: str, matched_rule: str):
        """
        记录安全拦截日志到 audit 文件。

        日志路径: logs/audit/YYYY-MM-DD.sqlguard.log

        参数:
            sql: 被拦截的 SQL 语句
            matched_rule: 匹配到的规则
        """
        try:
            os.makedirs(_AUDIT_LOG_DIR, exist_ok=True)
            today = datetime.now().strftime("%Y-%m-%d")
            log_file = os.path.join(_AUDIT_LOG_DIR, f"{today}.sqlguard.log")

            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_entry = (
                f"[{timestamp}] BLOCKED | 规则: {matched_rule} | "
                f"SQL: {sql[:200]}\n"
            )

            with open(log_file, "a", encoding="utf-8") as f:
                f.write(log_entry)

            logger.info(f"[SQLGuard] 审计日志已记录: {log_entry.strip()}")
        except Exception as e:
            logger.warning(f"[SQLGuard] 审计日志写入失败: {e}")

    @staticmethod
    def get_audit_logs(days: int = 7) -> list:
        """
        获取最近 N 天的审计日志。

        参数:
            days: 返回最近几天的日志，默认 7 天

        返回:
            日志条目列表
        """
        logs = []
        from datetime import timedelta

        today = datetime.now()
        for i in range(days):
            date_str = (today - timedelta(days=i)).strftime("%Y-%m-%d")
            log_file = os.path.join(_AUDIT_LOG_DIR, f"{date_str}.sqlguard.log")
            if os.path.exists(log_file):
                try:
                    with open(log_file, "r", encoding="utf-8") as f:
                        for line in f:
                            logs.append(line.strip())
                except Exception:
                    pass
        return logs
