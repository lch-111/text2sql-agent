# Text-to-SQL Agent 🤖

> **企业级智能数据分析 Agent** — 自然语言转 SQL、语义解析、术语映射、多模型路由、可视化图表

[![Docker](https://img.shields.io/badge/Docker-Ready-2496ed?logo=docker)](https://docker.com)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

---

## 📋 目录

- [项目概述](#项目概述)
- [系统架构](#系统架构)
- [核心组件](#核心组件)
- [工作流程](#工作流程)
- [技术亮点](#技术亮点)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [API 文档](#api-文档)
- [项目结构](#项目结构)

---

## 项目概述

**Text-to-SQL Agent** 是一个企业级智能数据分析系统，允许用户通过**自然语言提问**直接查询数据库，无需编写 SQL。系统自动完成以下链路：

```
用户提问 → 术语映射 → Schema 加载 → 语义解析 → SQL 生成 → 安全校验 → 执行 → 可视化
```

### 核心能力

| 能力 | 说明 |
|------|------|
| 🗣️ 自然语言 → SQL | 支持中文口语化提问，"广东卖了多少？" → 生成精确 SQL |
| 🔄 多模型路由 | 主模型 (deepseek) 生成 SQL，辅助模型 (qwen-turbo) 做语义解析/验证/解释 |
| 📊 18 种可视化图表 | 柱状图、折线图、饼图、散点图、雷达图、桑基图等 + 21 种风格配色 |
| 🧩 零硬编码术语映射 | 通过 `term_mappings.json` 配置同义词，如"广东省"→"广东" |
| 📚 Schema 自动加载 | 启动时从 MySQL INFORMATION_SCHEMA 读取所有表、字段、类型、注释 |
| 📖 字段实际值注入 | 自动读取每个文本字段的 DISTINCT 值，防止 SQL 使用不存在的值 |
| 🛡️ 多层安全校验 | SELECT 白名单、sqlglot 语法校验、引用修复 |
| ⚡ 两级语义缓存 | L1 精确匹配 + L2 语义相似度 (cosine > 0.9) 缓存，TTL 1h |
| 🔄 自我修正 | SQL 执行失败 → 错误分析 → 自动修正 → 重试（最多 2 次） |
| 💾 CSV/XLS/PNG 导出 | 查询结果可导出为 CSV/XLS，图表可下载为 PNG |

---

## 系统架构

```
┌──────────────────────────────────────────────────────────────────┐
│                        用户界面 (Web UI)                          │
│  ECharts 可视化  ·  流式 SSE 响应  ·  可折叠思维链  ·  多 Tab  │
└──────────────────────────┬───────────────────────────────────────┘
                           │ HTTP / SSE
┌──────────────────────────▼───────────────────────────────────────┐
│                     FastAPI 服务层 (app.py)                       │
│  /api/chat/stream  ·  /api/db/*  ·  /api/dashboard/*  ·  ...    │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                    Text-to-SQL Agent (agent.py)                   │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │
│  │ 术语映射  │  │Schema加载│  │语义解析   │  │ SQL 生成         │  │
│  │term_map. │→│INFORMA-  │→│qwen/deep │→│ deepseek-v4-pro   │  │
│  │json      │  │TION_SCHEMA│  │seek 解析  │  │ + 多模型共识     │  │
│  └──────────┘  └──────────┘  └──────────┘  └────────┬─────────┘  │
│                                                     │            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────▼──────────┐  │
│  │缓存: L1+L2│←│结果解释   │←│SQL 执行   │←│ 安全校验 + 引号   │  │
│  │ Redis     │  │qwen      │  │只读事务  │  │ 修复 + sqlglot   │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘  │
│                                                                  │
└──────────────────────────┬───────────────────────────────────────┘
                           │
┌──────────────────────────▼───────────────────────────────────────┐
│                      数据层                                       │
│  MySQL / PostgreSQL / SQLite  ·  Redis (缓存)                    │
└──────────────────────────────────────────────────────────────────┘
```

---

## 核心组件

### 1. 术语映射模块 (`data/term_mappings.json`)

零硬编码设计，所有同义词映射通过外部配置文件管理：

```json
{
  "value_mappings": {
    "province": {
      "广东省": "广东",
      "广西壮族自治区": "广西"
    }
  },
  "field_synonyms": {
    "order_amount": ["销售额", "营收", "流水"],
    "province": ["省", "省份", "地区"]
  }
}
```

系统在生成 SQL 前自动执行 `normalize_user_query()` 替换口语词汇。

### 2. Schema 自动加载

启动时从 MySQL `INFORMATION_SCHEMA` 动态读取：

```
【可用数据库表（共 2 个）】
表名: sales_order
  - amount (INTEGER)
  - province (VARCHAR(255))
  - order_date (VARCHAR(255))
  
表名: product_info
  - commodity_code (VARCHAR(255))
  - commodity_name (VARCHAR(255))
```

### 3. 字段实际值注入

对每个文本字段执行 `SELECT DISTINCT ... LIMIT 100`，将实际值列表注入 Prompt：

```
【字段实际值参考（用于精确匹配）】
  sales_order.province: [广东、江苏、浙江...]
```

模型自动匹配：用户说「广东省」→ 看到实际值只有「广东」→ WHERE province = '广东'

### 4. 多模型路由架构

| 任务 | 模型 | 说明 |
|------|------|------|
| 语义解析 | deepseek-v4-pro | 将自然语言解析为结构化意图 JSON |
| SQL 生成 | deepseek-v4-pro | 根据意图生成 SELECT 语句 |
| SQL 验证 | qwen-turbo (免费) | 检查字段名、值是否匹配 |
| 结果解释 | qwen-turbo (免费) | 将数据转为自然语言结论 |

**降级策略**：辅助模型任意步骤失败 → 跳过，主模型直接生成。

### 5. SQL 后处理与安全

```
原始: SELECT * FROM 'sales_order' o JOIN 'product_info' pi
  →  Pattern 1: JOIN `product_info` pi
  →  Pattern 2-6: 修复 ON/AS/SELECT 等引号
  →  Pattern 7: 保护 WHERE 字符串值
最终: SELECT * FROM `sales_order` o JOIN `product_info` pi
```

- **安全校验**：只允许 SELECT，禁止 INSERT/UPDATE/DELETE/DROP
- **语法校验**：sqlglot 解析 + 表/字段存在性检查
- **提取纯 SQL**：从思维链文本中精确提取 SQL 代码块

### 6. 可视化系统

18 种图表类型 × 21 种配色风格，用户可交互式选择：

| 类型 | 说明 |
|------|------|
| 柱状图/折线图/饼图 | 基础统计 |
| 散点图/漏斗图/雷达图 | 多维分析 |
| 树图/热力图/桑基图 | 层次/关系 |
| K线图/箱线图/平行坐标 | 金融/统计 |
| 旭日图/主题河流/关系图 | 高级可视化 |

---

## 工作流程

### 完整查询链路

```
用户: "广东省销售额"
  │
  ├── ① 术语预处理
  │    normalize_user_query("广东省销售额")
  │    → "广东销售额"（"广东省" → "广东"）
  │
  ├── ② Schema 加载
  │    _load_db_schema() → sales_order, product_info
  │    _load_distinct_values() → province: [广东,江苏,浙江...]
  │
  ├── ③ 语义解析 (deepseek)
  │    {"intent":"aggregation","primary_table":"sales_order",
  │     "filters":[{"field":"province","value":"广东"}],
  │     "aggregation":{"func":"SUM","field":"amount"}}
  │
  ├── ④ SQL 生成 (deepseek)
  │    SELECT SUM(`amount`) AS `total_sales`
  │    FROM `sales_order` WHERE `province` = '广东'
  │
  ├── ⑤ 安全校验 + 引号修复
  │    SQLValidator.validate() ✓
  │    fix_sql_quoting() ✓
  │
  ├── ⑥ 执行 SQL → 返回结果
  │
  └── ⑦ 可视化渲染
       用户选择图表类型 → 18 种可选 → 21 种配色
```

---

## 快速开始

### 前置条件

- Docker & Docker Compose
- 或 Python 3.12+（本地运行）
- MySQL 数据库（可选，默认使用内置 SQLite）

### 方式一：Docker 部署（推荐）

```bash
# 1. 克隆项目
git clone https://github.com/lch-111/text2sql-agent.git
cd text2sql-agent

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 API Key 和数据库配置

# 3. 启动
docker compose up -d

# 4. 打开浏览器
# http://localhost:8000
```

### 方式二：本地运行

```bash
# 1. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# 或 .venv\Scripts\activate  # Windows

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 .env
cp .env.example .env
# 编辑 .env

# 4. 启动
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

### 方式三：仅使用 API

```python
from agent import TextToSQLAgent

agent = TextToSQLAgent()

result = agent.generate_and_execute("广东省销售额")

print(result["sql"])
# SELECT SUM(`amount`) FROM `sales_order` WHERE `province` = '广东'

print(result["result"])
# [{"total_sales": 3347000}]
```

---

## 配置说明

### 环境变量 (`.env`)

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_PROVIDER` | `openai` | LLM 提供商 `openai`/`ollama` |
| `OPENAI_API_KEY` | - | API Key |
| `OPENAI_BASE_URL` | - | API 地址（如阿里云 DashScope） |
| `OPENAI_MODEL` | `deepseek-v4-pro` | 主模型 |
| `DB_TYPE` | `sqlite` | 数据库类型 |
| `MYSQL_HOST` | `host.docker.internal` | MySQL 地址 |
| `MYSQL_DATABASE` | - | 数据库名 |

### 辅助模型配置

```bash
# 三个任务可分别指定不同模型（默认用同一个）
AUX_DEFAULT_MODEL=qwen-turbo
AUX_INTENT_MODEL=qwen-turbo      # 语义解析
AUX_VALIDATE_MODEL=qwen-turbo    # SQL 验证
AUX_EXPLAIN_MODEL=qwen-turbo     # 结果解释
```

### 术语映射 (`data/term_mappings.json`)

```json
{
  "value_mappings": {
    "province": { "广东省": "广东" }
  },
  "field_synonyms": {
    "order_amount": ["销售额", "营收"]
  }
}
```

---

## API 文档

### 流式聊天

```http
POST /api/chat/stream
Content-Type: application/json

{"question": "广东省销售额"}
```

SSE 事件流：

```
event: step    → {"step":"semantic_parse","message":"解析查询意图..."}
event: token   → {"text":"SELECT SUM(amount)..."}
event: sql     → {"sql":"SELECT SUM(amount)..."}
event: result  → {"result":[...],"columns":[...]}
event: done    → {}
```

### 其他接口

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/db/status` | GET | 数据库连接状态 |
| `/api/db/connect` | POST | 连接数据库 |
| `/api/dashboard/tables` | GET | 获取表列表 |
| `/api/dashboard/chart-data` | POST | 获取图表数据 |
| `/api/cache/stats` | GET | 缓存命中率统计 |
| `/api/eval/report` | GET | 评估报告 |

---

## 项目结构

```
text2sql-agent/
├── agent.py              # Text-to-SQL Agent 核心（语义解析 + SQL 生成）
├── app.py                # FastAPI 应用入口
├── config.py             # 全局配置（多模型 + 缓存 + 数据库）
├── database.py           # 数据库适配层（MySQL/SQLite/PostgreSQL）
├── cache.py              # L1 精确缓存 + L2 语义缓存 (Redis)
├── sql_validator.py      # sqlglot 语法校验 + 表/字段存在性检查
├── evaluator.py          # Golden Dataset 自动化评估
├── file_processor.py     # PDF/Excel 文件解析
├── hybrid_search.py      # BM25 + 向量混合检索
├── vector_store.py       # TF-IDF 向量存储
├── tracing.py            # LangSmith 链路追踪
├── dashboard.py          # 数据大屏 Streamlit 版本
│
├── api/
│   ├── routes.py         # API 路由定义
│   └── streaming.py      # SSE 流式聊天处理
│
├── utils/
│   └── model_router.py   # 多模型路由 + 降级
│
├── static/
│   ├── css/style.css     # 浅色主题样式
│   └── js/
│       ├── chat.js       # 聊天 UI + 图表选择器
│       ├── charts.js     # 18 种图表类型 + 21 种配色
│       ├── app.js        # 主 UI 逻辑
│       └── utils.js      # 工具函数
│
├── templates/
│   └── index.html        # 主页面模板
│
├── data/
│   ├── term_mappings.json  # 术语映射配置
│   └── term_mapping.json   # (兼容旧版)
│
├── Dockerfile            # Docker 构建
├── docker-compose.yml    # Docker Compose
├── entrypoint.sh         # 容器入口
└── requirements.txt      # Python 依赖
```

---

### 架构设计

- **为什么用多模型路由而非单一模型？** 降低 API 成本：免费模型 (qwen-turbo) 处理辅助任务，付费模型 (deepseek) 仅用于核心 SQL 生成
- **如何处理用户口语与数据库值的差异？** 三层机制：术语映射 → 字段实际值注入 → 语义解析强制匹配
- **如何保证 SQL 安全？** 多层防线：SELECT 白名单 → 正则关键词过滤 → sqlglot 语法解析 → 引号修复

### 关键技术决策

| 决策 | 选择 | 理由 |
|------|------|------|
| LLM 协议 | OpenAI 兼容 | 可切换任意 OpenAI 兼容 API |
| 缓存 | Redis + 语义向量 | 同类问题自动复用，节省 Token |
| Schema 加载 | INFORMATION_SCHEMA | 自动适配任意 SQL 数据库 |
| 图表引擎 | ECharts | 18 种图表类型，高度可定制 |
| 流式响应 | Server-Sent Events | 实时显示生成过程，提升体验 |

### 优化方向

- **性能**：引入 FAISS 向量索引加速 L2 缓存检索
- **准确性**：增加 Self-Consistency 多轮验证
- **扩展性**：支持更多数据库（StarRocks、ClickHouse）
- **NL2SQL 评估**：集成 Spider/ Bird 数据集基准测试

---

## 许可证

MIT License

---

*本项目为 AI Agent 开发面试作品，展示了 Text-to-SQL、多模型路由、语义解析、可视化等完整技术栈。*
