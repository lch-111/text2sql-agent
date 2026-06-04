---
name: text2sql-agent
description: Text-to-SQL Agent — NL2SQL、SQL 优化、术语映射、多模型路由、18种图表、多Agent编排
---

# Text-to-SQL Agent Skill (增强版 v2)

## 概述

Text-to-SQL Agent 是**专业数据库智能分析助手**，完整执行链路如下：

```
用户问题 → 术语库预处理 → 语义解析 → 反问/澄清 → SQL 生成 
  → 安全校验 → 引号修复 → SQL 优化分析 → 执行 → 结果解读 → 缓存
```

## 架构组件

| 组件 | 来源 | 核心职责 |
|------|------|----------|
| 术语预处理 | `term_mappings.json` | 替换口语→数据库值，零硬编码 |
| Schema 加载 | MySQL INFORMATION_SCHEMA | 动态读取表/字段/类型/主键/外键 |
| 字段实际值 | SELECT DISTINCT LIMIT 100 | 注入实际值，防止模型幻觉 |
| 语义解析 | deepseek-v4-pro | NL→结构化意图 JSON |
| SQL 生成 | deepseek-v4-pro | 意图 + Schema → SELECT |
| SQL 优化 | `sql_optimizer.py` ✨新增 | 13 条规则 + 索引建议 |
| 安全校验 | SQLValidator | 只允许 SELECT |
| 引号修复 | `fix_sql_quoting()` | 6 层正则修复 LLM 引号错误 |
| 反思修正 | qwen-turbo | 0 行结果时分析原因并重试 |
| 结果解释 | qwen-turbo | 表格→自然语言结论 |
| 缓存 | Redis L1+L2 语义 | 相似问题 >0.9 直接返回 |

## 外部技能整合

### 1. NL2SQL — 多 Agent 验证器 (agent.py)
- **逻辑验证**: 检查 JOIN 条件、GROUP BY 一致性、聚合函数用法
- **性能验证**: 检测全表扫描、缺少索引、N+1 查询模式
- **安全验证**: SQL 注入防护、SELECT 白名单、敏感数据保护

### 2. SQL 优化器 (sql_optimizer.py) ✨新增
- 13 条优化规则: SELECT *、LIKE 前导通配符、ORDER BY RAND()、OFFSET 大偏移等
- 4 种索引建议: WHERE/JOIN/ORDER BY/GROUP BY 字段
- 自动修复: 自动添加 LIMIT（聚合查询除外）
- 调用示例:
  ```python
  from sql_optimizer import optimize_sql
  result = optimize_sql("SELECT * FROM orders")
  # → "避免 SELECT *" + "缺少 LIMIT → 已自动添加 LIMIT 100"
  ```

### 3. Anthropic MySQL 安全规则
- PREPARE/EXECUTE 动态 SQL 拦截
- INFORMATION_SCHEMA 查询审计
- 敏感列（密码、token）自动脱敏
- 事务隔离级别检查

### 4. MCP 多数据库适配参考 (database.py)
- 统一接口: MySQL / PostgreSQL / SQLite 一键切换
- 自动方言检测: sqlglot 动态切换 dialect
- 连接池管理: 自动重连 + 超时控制

### 5. SQLens 向量记忆设计 (cache.py)
- L1 MD5 精确缓存
- L2 jieba 词频向量语义缓存 (cosine > 0.9)
- 缓存 TTL: 1 小时

## 核心工作流程

### 完整查询链路

```
用户: "广东省销售额"
  │
  ├── ① 术语预处理 (normalize_user_query)
  │    "广东省" → "广东"
  │
  ├── ② Schema + 字段值加载
  │    INFORMATION_SCHEMA + SELECT DISTINCT
  │
  ├── ③ 语义解析 (deepseek)
  │    {"filters":[{"field":"province","value":"广东"}], ...}
  │
  ├── ④ SQL 生成 (deepseek)
  │    SELECT SUM(amount) FROM sales_order WHERE province = '广东'
  │
  ├── ⑤ 安全校验 (SQLValidator)
  │    ✓ 只允许 SELECT  ✓ 无危险关键字
  │
  ├── ⑥ 引号修复 (fix_sql_quoting)
  │    JOIN 'product_info' → JOIN `product_info`
  │
  ├── ⑦ SQL 优化分析 (sql_optimizer) ✨
  │    → 检查 13 条规则 → 索引建议
  │
  ├── ⑧ 执行 SQL
  │
  └── ⑨ 结果反思 + 解释
        0 行 → qwen 分析 → 修正 → 重试
```

## 外部仓库参考

```
external_skills/
├── NL2SQL/                    # 多 Agent 编排 + 验证器
├── antigravity-awesome-skills/ # SQL 优化模式
├── sqllens/                   # 向量记忆设计
├── mcp-database-server/       # MCP 多 DB 适配
└── skills/                    # Anthropic 官方 MySQL 技能
```

## 快速开始

```python
from agent import get_agent
from sql_optimizer import optimize_sql

agent = get_agent()
result = agent.generate_and_execute("广东省销售额")

# SQL 优化分析
opt = optimize_sql(result["sql"])
for s in opt["suggestions"]:
    print(f"[{s['severity']}] {s['message']}")
```
