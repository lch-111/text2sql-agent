---
name: text2sql-agent
description: Text-to-SQL Agent — 多Agent编排(LangGraph) · FieldResolver · SQLGuard安全拦截 · Self-Correction · 莫兰迪UI
---

# Text-to-SQL Agent Skill (v4 — 多 Agent 协作 + 零静态映射)

## 概述

Text-to-SQL Agent 是**专业数据库智能分析助手**，基于 LangGraph 多 Agent 协作系统。

```
用户问题 → ConversationManager(多轮补全)
  → Router → (chat|dangerous|query)
  → query → Cache(L1/L2) → SchemaRetriever + FieldResolver
  → GeneratorAgent → CriticAgent(Trace检索) → SQLGuard → Executor
  → 结果返回 + 上下文保存
```

## 架构组件

### 多 Agent 系统 (agents/)

| Agent | 模型 | 职责 |
|-------|------|------|
| Router | GLM-4-Flash | 意图分类：query/chat/dangerous |
| SchemaRetriever | GLM-4-Flash | Schema 检索 + FieldResolver 字段/值解析 + Reranker |
| Generator | DeepSeek-v4-Flash | SQL 生成（零静态映射，禁止表名别名） |
| Critic | GLM-4-Flash | SQL 校验 + Trace 检索 + Self-Correction |
| Executor | — | SQLGuard 拦截 + 只读执行 + SQL 优化 |
| ConversationManager | GLM-4-Flash | 多轮对话追问补全 |

### 状态图编排 (graph.py)

```
context_completion → router → (chat|dangerous|query)
query → check_cache → retrieve_schema → generator → critic
critic → (valid → executor | invalid → generator)
executor → write_cache → save_context → END
```

### 安全体系

1. **Router Agent** — 意图级拦截危险操作
2. **Generator Prompt** — 约束只生成 SELECT，禁止表名别名
3. **SQLGuard** — `startswith(('SELECT','WITH'))` + DDL/DML 黑名单
4. **ADMIN_TOKEN** — 管理接口 SHA256 认证
5. **速率限制** — `/api/chat/stream` 每 IP 3 req/s

### 字段解析 (FieldResolver)

```
resolve_field("销售额", "orders")
  ├── KV 缓存 → 直接返回
  ├── 规则匹配（字段名/注释/样本值）→ 90% 零 Token
  └── GLM-4-Flash 消歧 → 写入缓存
```

### 数据缓存

- **L1 精确缓存**：MD5 哈希
- **L2 语义缓存**：jieba 词频向量相似度 > 0.9

### 前端 UI

- 莫兰迪配色（雾霾蓝 `#8A9BAE`）、Geist 字体、12px 大圆角
- 亮/暗双主题切换，0.3s 平滑过渡
- 品牌首页 → 三栏工作台（历史对话 / 聊天 / 工具配置）
- 两侧栏可折叠，按钮固定在屏幕边缘

## 环境变量

见 `.env.example`：
- `OPENAI_API_KEY` / `OPENAI_BASE_URL` — DeepSeek 模型
- `GLM_API_KEY` / `GLM_BASE_URL` — GLM 模型
- `ADMIN_TOKEN` — 管理令牌
- `ROUTER_MODEL` / `GENERATOR_MODEL` / `CRITIC_MODEL` / `RERANKER_MODEL`

## 快速开始

```python
from agent import get_agent
agent = get_agent()
result = agent.generate_and_execute("广东省销售额")
print(result["sql"])       # SELECT SUM(amount) FROM `orders` WHERE `province` = '广东'
print(result["result"])    # 查询结果
```
