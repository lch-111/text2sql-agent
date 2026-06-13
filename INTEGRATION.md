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
