# Trace: 省份字段值映射错误导致查询结果为空

**记录时间**：2025-06-09 14:32
**发现人**：AI（Self-Correction）

---

## 触发场景

- **用户原始输入**："广东省的销售额是多少"
- **Agent 生成的 SQL**：
  ```sql
  SELECT SUM(total_amount) AS total_sales
  FROM orders
  WHERE province = '广东省' AND status = '已完成'
  ```
- **执行结果**：返回 0 行

---

## 原始报错

无执行报错，但结果为空（0 rows returned）。

---

## 根本原因分析

- **直接原因**：数据库 `province` 字段中存储的值为 `'广东'`（不含"省"字），
  但 Agent 直接使用了用户口语中的 `'广东省'` 作为过滤条件。
- **深层原因**：
  1. 术语映射表 `term_mappings.json` 中虽有 `"广东省" → "广东"`，但本次未生效
  2. Agent 在生成 SQL 时未先查询 `SELECT DISTINCT province` 确认实际值
- **受影响组件**：`GeneratorAgent`（SQL 生成）、`SchemaRetriever`（字段值检索）

---

## 修复过程

### Self-Correction 第 1 次

- 分析出 `province = '广东省'` 无匹配，应改为 `province = '广东'`
- 修正后的 SQL：
  ```sql
  SELECT SUM(total_amount) AS total_sales
  FROM orders
  WHERE province = '广东' AND status = '已完成'
  ```
- 执行结果：**成功**，返回 125 行

---

## 最终解决方案

1. **确保 `normalize_user_query` 在 Generator 前执行**
   - 验证：`SchemaRetriever.normalize_query("广东省的销售额")` → `"广东的销售额"`
   - 标准化后的查询值直接替换用户输入中的口语词

2. **在 Generator 的 Prompt 中注入字段实际值列表**
   - 将 `SELECT DISTINCT province FROM orders` 的结果作为常量列表注入
   - Agent 看到实际值列表为 `[广东, 江苏, 浙江, ...]`，不应使用"广东省"

3. **无需修改术语库**
   - `term_mappings.json` 中已有 `"广东省" → "广东"` 映射
   - 问题在于流程中 normalize 步骤未被触发

---

## 预防措施

- [x] 修复流程顺序：确保 `normalize_query()` 在 `GeneratorAgent.generate()` 之前调用
- [x] 在 Generator Prompt 末尾增加强制指令：
      "⚠️ WHERE 条件中的文本值必须与【字段实际值参考】中的值完全一致"
- [ ] 添加单元测试：测试 `province = '广东省'` → 被修正为 `'广东'`
- [ ] 更新 `PROJECT_CONTEXT.md` 中的"常见陷阱"表格

---

## 相关 Trace 链接

- 关联 SOP：`skills/self-correction-sop.md`
- 关联文档：`PROJECT_CONTEXT.md` → 4.3 常见陷阱
