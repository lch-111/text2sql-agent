# sql-safety-rules.md — SQL 安全物理拦截黑名单

## 目标
在代码层面绝对阻止任何可能修改数据库结构或数据的 SQL 语句执行，即使 LLM 产生了幻觉。

## 拦截位置
- **拦截点**：`SQLExecutor.execute(sql: str)` 方法，在建立数据库游标执行前调用 `SQLGuard.validate(sql)`。

## 黑名单规则（正则匹配，不区分大小写）

### DDL 语句（结构修改）
CREATE TABLE | ALTER TABLE | DROP TABLE | TRUNCATE TABLE
CREATE INDEX | DROP INDEX
CREATE VIEW | DROP VIEW
CREATE SCHEMA | DROP SCHEMA

text

### DML 写操作
INSERT INTO | UPDATE | DELETE FROM
MERGE INTO
REPLACE INTO

text

### 权限与系统操作
GRANT | REVOKE | SET PASSWORD
EXECUTE | CALL -- 禁止执行存储过程（可能含写操作）
LOAD DATA INFILE

text

## 校验逻辑伪代码

```python
class SQLGuard:
    blacklist = [
        r"\bCREATE\s+TABLE\b",
        r"\bALTER\s+TABLE\b",
        r"\bDROP\s+TABLE\b",
        ...
    ]

    @staticmethod
    def validate(sql: str) -> bool:
        """若返回 False，则抛出 SQLSafetyError，阻断执行"""
        for pattern in SQLGuard.blacklist:
            if re.search(pattern, sql, re.IGNORECASE):
                logger.critical(f"危险 SQL 被拦截: {sql[:100]}")
                raise SQLSafetyError(f"禁止执行危险操作：匹配规则 {pattern}")
        return True
例外处理
即使 SQL 中只包含“注释”内的危险关键词，也一并拦截（安全优先）。

如果需要执行合法的写操作（如管理员通过后台工具维护），必须：

通过独立的安全模块接口。
二次认证（如临时 Token）。
集成要求
该模块必须在系统启动时加载，不可被绕过。

拦截日志单独存储，用于审计。
