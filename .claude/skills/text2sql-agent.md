# Text-to-SQL Agent Skill (增强版 v2)

---
name: text2sql-agent
description: Text-to-SQL Agent — 增强版数据库智能分析助手，自然语言转 SQL、意图消歧、自我修正、术语库映射、SQL 语法纠错

## 概述

Text-to-SQL Agent 是**专业数据库智能分析助手**，完整执行链路如下：
用户问题 → 术语库预处理（口语→数据库实际值） → 语义解析 → 反问/澄清 → SQL 生成 → 安全校验（提取纯SQL） → SQL 后处理（引号修正） → 执行 → 结果解读 → 缓存

## 架构组件

| 组件 | 依赖模型/引擎 | 核心职责 |
| ---- | ------------- | -------- |
| 术语预处理 | 规则引擎 | 加载 `term_mappings.json`，替换查询中的口语词汇 |
| 语义解析 | qwen-turbo | 结合表结构，将自然语言解析为结构化意图JSON |
| SQL 生成 | deepseek-v4-pro | 基于意图、表结构、字段真实值生成 SELECT 语句 |
| SQL 验证 | qwen-turbo（可选） | 校验查询条件值与数据库字段实际值是否匹配 |
| 结果解释 | qwen-turbo | 将查询数据转换为自然语言描述 |
| 缓存 | L1精确缓存 + L2语义缓存 | 缓存标准化问题、SQL、查询结果，有效期1小时 |

## 核心能力说明

### 1. 术语库（零硬编码）

- 文件路径：`data/term_mappings.json`
- 数据结构示例

```json
{
  "value_mappings": {
    "province": {
      "广东省": "广东",
      "广西壮族自治区": "广西"
    }
  },
  "field_synonyms": {
    "order_amount": ["销售额", "营收"]
  }
}
```

- 使用规则：生成SQL前调用 `normalize_user_query()`，统一替换口语关键词为数据库标准值。

### 2. 字段实际值自动加载

- 启动逻辑：启动时执行 `SELECT DISTINCT col FROM table LIMIT 100`，缓存所有文本字段的真实数据值。
- 作用：将字段值列表注入提示词，约束模型仅使用库内已有数据构建查询条件，避免虚构值。
- 缓存策略：默认5分钟自动刷新，支持手动调用 `_load_field_values(force=True)` 强制刷新。

### 3. 数据表结构(Schema)自动加载

- 加载逻辑：启动后连接数据库，从 `INFORMATION_SCHEMA` 读取全量表、字段信息并缓存至内存。
- 数据格式示例

```json
{
  "orders": {
    "columns": [
      {"name": "id", "type": "bigint", "comment": "主键"},
      {"name": "amount", "type": "decimal(10,2)", "comment": "订单金额"}
    ],
    "primary_key": "id"
  }
}
```

### 4. 语义解析 Prompt

- 注入内容：完整表结构JSON、字段实际值列表、术语映射规则。
- 强制规则：解析结果中筛选条件的值，必须取自字段实际值列表，禁止使用原始口语词汇。
- 输出格式：标准化结构化JSON。

### 5. SQL 生成 Prompt

- 注入内容：数据库类型、表结构、字段真实值、术语规则、语义解析结果。
- 硬性约束：
  1. 仅可使用已加载的表名、字段名；
  2. `WHERE` 条件文本值必须选用库内真实值；
  3. 普通英文表/字段名不添加引号，含特殊字符/关键字使用**反引号 `` ` ``**；
  4. 字符串内容统一使用**单引号 `'`**；
  5. 禁止用单引号包裹表名、字段名。

### 6. SQL 后处理函数 `fix_sql_quoting`

专门修正LLM生成的引号错误，示例：

- 错误：`JOIN 'sales_order' o` → 修正：`JOIN `sales_order` o`
- 错误：`'o' .product_number` → 修正：`o`.product_number
- 错误：`SUM('amount')` → 修正：`SUM(`amount`)`
- 保留规则：`WHERE` 内字符串值（如 `province = '广东'`）不作改动。
- 执行要求：SQL 执行前**强制调用**，作为语法校验最后防线。

### 7. 安全校验 & SQL 提取

1. 提取纯SQL：调用 `_extract_sql()`，从包含思维链的文本中，截取以 `SELECT`/`WITH` 开头的标准SQL；
2. 只读校验：仅允许查询语句，拦截 `INSERT/UPDATE/DELETE/DROP` 等改写、删改类操作；
3. 执行顺序：提取SQL → 安全校验 → 引号后处理 → 执行查询。

### 8. 降级与缓存策略

- 降级规则：语义解析、SQL验证、结果解释等辅助环节失败，直接跳过并由主模型生成SQL；**SQL生成主模型失败则直接报错，不降级**。
- 缓存规则：语义相似度阈值 0.9，数据有效期(TTL) 1小时。

## 快速开始

```python
from agent import get_agent

agent = get_agent()
result = agent.generate_and_execute("广东省销售额")

# 内部执行流程
# 1. 术语预处理："广东省销售额" → "广东销售额"
# 2. 语义解析：筛选条件值设为 "广东"
# 3. SQL 生成：拼接条件 WHERE province = '广东'
# 4. 引号修正：fix_sql_quoting 统一语法格式
# 5. 执行SQL并返回结果
```

## 注意事项

1. 思维链类文本（如「▶ 展开思维链」）需放置在SQL代码块外部，避免干扰SQL提取；
2. 若仍出现引号错误，优先核查Prompt引号规则，并确认 `fix_sql_quoting` 为最新版本；
3. 字段真实值缓存支持自动/手动刷新，可根据数据更新频率调整策略。