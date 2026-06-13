# self-correction-sop.md — 自我修正标准作业程序

## 适用场景
当 SQL Generator 生成的 SQL 执行出错时，启动本 SOP，避免将原始数据库错误直接展示给用户。

## 重试策略

### 输入
- `original_query`：用户原始自然语言问题
- `failed_sql`：执行失败的 SQL 语句
- `error_message`：数据库返回的错误信息（如 `unknown column 'total' in 'field list'`）
- `schema_context`：当前涉及的表结构（CREATE TABLE 语句）

### 流程
1. **记录首次错误**：创建 Trace 记录（参见 `traces/trace-template.md`）。
2. **构建修正 Prompt**：
你是一名 SQL 修复专家。用户提问：“{original_query}”。
你之前生成了以下 SQL，但执行失败。
SQL：{failed_sql}
错误信息：{error_message}
相关表结构：{schema_context}

请分析错误原因，并生成一个修正后的、仅包含 SELECT 的 SQL 语句。
只输出 SQL，不要解释。

text
3. **调用 Generator Agent** 生成修正 SQL。
4. **再次执行**：
- 若成功，返回结果并关闭 Trace。
- 若再次失败：
  - 若重试次数 < MAX_RETRY（2 次），重复步骤 2，将新的错误信息追加到 Prompt 中。
  - 若达到最大重试次数，终止循环，返回友好提示：“抱歉，我暂时无法完成这个查询，请联系管理员。” 并记录 Trace。

### 约束
- 每次重试必须记录在 Trace 中。
- 修正 Prompt 必须保留原始错误信息，帮助模型聚焦问题。
- 修正过程中不允许执行任何 DDL/DML 操作。

### 流程图
┌──────────────┐ 失败 ┌──────────────────┐
│ 执行 SQL ├──────────►│ 构建修正 Prompt │
└──────┬───────┘ └────────┬─────────┘
│成功 │
▼ ▼
返回结果 ┌──────────────┐
│ 重试执行 │
└──┬───────────┘
│失败且次数<2
└───循环───────┘
│次数>=2
▼
友好提示 + Trace