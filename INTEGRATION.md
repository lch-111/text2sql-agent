# 外部技能整合说明 — INTEGRATION GUIDE

## 概述

从 5 个开源仓库中提取核心能力，整合到现有 Text-to-SQL Agent 项目中。
所有新增功能保留原有接口，通过配置开关启用。

---

## 整合来源

| # | 仓库 | 整合内容 | 对应文件 |
|---|------|---------|---------|
| 1 | [NL2SQL](https://github.com/ToheedAsghar/NL2SQL) | 多 Agent 编排模式、逻辑/性能/安全验证器 | `agent.py` (反思循环增强) |
| 2 | [antigravity-awesome-skills](https://github.com/sickn33/antigravity-awesome-skills) | SQL 优化规则、索引建议、执行计划分析 | `sql_optimizer.py` (新建) |
| 3 | [sqllens](https://github.com/The01Geek/sqllens) | 向量记忆 + 自然语言查询接口设计思路 | `cache.py` (语义缓存增强) |
| 4 | [mcp-database-server](https://github.com/executeautomation/mcp-database-server) | 多数据库适配器模式 | `database.py` (多 DB 切换增强) |
| 5 | [Anthropic Official Skills](https://github.com/anthropics/skills/tree/main/skills) | MySQL 安全执行 + SQL 优化模式 | `sql_optimizer.py` + `agent.py` |

---

## 新增文件

| 文件 | 说明 |
|------|------|
| `sql_optimizer.py` | SQL 查询优化器 — 13 条优化规则 + 4 种索引建议 + 自动修复 |
| `INTEGRATION.md` | 本文档 |

---

## 修改的文件

### `agent.py`
- 增强 `run()` 中的结果反思循环，集成优化器检查
- 新增可选配置 `enable_sql_optimizer`

### `sql_validator.py`
- 增强语法校验：增加对 JOIN 条件缺失、GROUP BY 不一致等逻辑错误的检测

### `database.py`
- 参考 MCP 多数据库适配模式，增强 PostgreSQL/SQLite 切换
- 新增 `get_sql_dialect()` 方法

---

## SQL 优化器使用

```python
from sql_optimizer import optimize_sql, suggest_indexes

# 分析一条 SQL
result = optimize_sql("SELECT * FROM orders o JOIN users u ON o.user_id = u.id WHERE u.name LIKE '%abc%'")

for s in result["suggestions"]:
    print(f"[{s['severity']}] {s['message']}")

for idx in result["indexes"]:
    print(f"📌 {idx['suggestion']}")
```

### 检测的优化规则

| ID | 严重度 | 说明 |
|----|--------|------|
| SELECT_STAR | ⚠️ | 避免 SELECT * |
| MISSING_WHERE | 🔴 | 缺少 WHERE 条件 |
| LIKE_LEADING_WILDCARD | ⚠️ | LIKE '%...' 无法使用索引 |
| NEGATION_IN_WHERE | ⚠️ | 否定条件无法使用索引 |
| ORDER_BY_RAND | 🔴 | ORDER BY RAND() 全表扫描 |
| NO_LIMIT | ⚠️ | 缺少 LIMIT |
| FUNCTION_ON_COLUMN | ⚠️ | 字段上使用函数阻止索引 |
| OFFSET_LARGE | ⚠️ | 大偏移量分页 |
| SELECT_IN_SELECT | ⚠️ | SELECT 中的子查询 |
| IN_SUBQUERY | ℹ️ | IN (SELECT) 性能较差 |
| IMPLICIT_TYPE_CAST | ℹ️ | 隐式类型转换 |
| DISTINCT_JOIN | ℹ️ | DISTINCT + JOIN 可能产生重复 |
| GROUP_BY_NONINDEXED | ℹ️ | GROUP BY 字段需索引 |
| OR_WITHOUT_INDEX | ℹ️ | OR 条件可能无法使用索引 |

---

## 配置开关

在 `.env` 中添加：

```bash
# SQL 优化器
ENABLE_SQL_OPTIMIZER=true
```

在 `config.py` 中添加：

```python
@dataclass
class AgentConfig:
    enable_sql_optimizer: bool = os.getenv("ENABLE_SQL_OPTIMIZER", "true").lower() == "true"
```

---

## 原始仓库参考

```
external_skills/
├── NL2SQL/                          # 多 Agent 编排
├── antigravity-awesome-skills/      # SQL 优化技能
├── sqllens/                         # 向量记忆设计
├── mcp-database-server/             # MCP 多数据库适配
└── skills/                          # Anthropic 官方技能
    └── skills/
        ├── mysql/                   # MySQL 安全执行
        └── sql-optimization-patterns/ # SQL 优化模式
```
