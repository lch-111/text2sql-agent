# query-writing-sop.md — 复杂查询标准作业流程

> 本文档指导 Agent 编写复杂 SQL 查询时的思维链路和注意事项。
> 每次编写涉及多表 JOIN、聚合、时间范围或嵌套子查询的 SQL 时，
> 必须按以下步骤检查。

---

## 1. 查询编写五步法

### Step 1：理解用户意图

将用户问题拆解为结构化要素：

| 要素 | 要确认的问题 | 示例 |
|------|-------------|------|
| 查询对象 | 用户想要什么数据？是明细还是汇总？ | "每个省份的销售额" → 聚合汇总 |
| 过滤条件 | 有哪些筛选条件？时间范围？状态？ | "上个月广东" → status='已完成', province='广东', order_date 范围 |
| 排序要求 | 需要排序吗？升序还是降序？ | "前 5 名" → ORDER BY ... DESC LIMIT 5 |
| 分组维度 | 按什么维度聚合？ | "按省份" → GROUP BY province |
| 表来源 | 需要哪几张表？JOIN 条件是什么？ | orders + users → user_id 关联 |

### Step 2：确认表关联路径

根据 PROJECT_CONTEXT.md 中的表关联关系确定 JOIN 路径：

```
用户问"每个商品类别的销售额"
检查：orders 有 product_id → products 有 product_id → JOIN 路径成立
结果：orders JOIN products ON o.product_id = p.product_id
```

**自检清单**：
- [ ] JOIN 条件是否正确？是否存在笛卡尔积风险？
- [ ] 是否所有涉及的字段都在对应的表中？
- [ ] LEFT JOIN 还是 INNER JOIN？NULL 值如何处理？

### Step 3：验证聚合逻辑

当 SQL 包含聚合函数（SUM/COUNT/AVG/MAX/MIN）时：

```sql
-- ✅ 正确模式
SELECT u.province, SUM(o.total_amount) AS total_sales
FROM orders o JOIN users u ON o.user_id = u.user_id
WHERE o.status = '已完成'
GROUP BY u.province
HAVING total_sales > 10000
ORDER BY total_sales DESC
LIMIT 10
```

**聚合规则**：
- SELECT 中的非聚合字段 → 必须在 GROUP BY 中
- HAVING 用于聚合后过滤 → WHERE 用于聚合前过滤
- `COUNT(*)` 包含 NULL → `COUNT(column)` 不包含 NULL
- `SUM(NULL)` 返回 NULL → 使用 `COALESCE(SUM(column), 0)`

### Step 4：确认过滤条件

**常见过滤模式**：

| 场景 | 正确写法 | 错误写法 |
|------|---------|---------|
| 时间范围 | `order_date >= '2024-01-01' AND order_date < '2024-02-01'` | `YEAR(order_date)=2024`（无法使用索引） |
| 字符串匹配 | `province = '广东'` | `province LIKE '%广东%'`（全表扫描） |
| IN 列表 | `status IN ('已完成', '待发货')` | `status = '已完成' OR status = '待发货'` |
| 排除空值 | `column IS NOT NULL` | `column != ''`（可能漏掉 NULL） |
| 布尔值 | `is_active = 1` | `is_active = '是'`（字段是 INTEGER） |

### Step 5：检查安全与性能

- [ ] 只包含 SELECT 语句？没有 INSERT/UPDATE/DELETE/DROP？
- [ ] 表名和字段名在 schema 中真实存在？
- [ ] 非聚合查询有 LIMIT 限制？
- [ ] 引号使用正确（表名不用单引号，字符串值用单引号）？
- [ ] JOIN 条件没有漏掉关联字段导致笛卡尔积？
- [ ] 业务口径正确（如 status='已完成' 才计入销售额）？

---

## 2. 常见查询模式速查

### 2.1 按维度聚合

```sql
SELECT {维度字段}, {聚合函数} AS {别名}
FROM {事实表} f
JOIN {维度表} d ON f.{外键} = d.{主键}
WHERE {过滤条件} AND f.status = '已完成'
GROUP BY {维度字段}
ORDER BY {别名} DESC
LIMIT {限制条数}
```

### 2.2 时间趋势分析

```sql
SELECT DATE_FORMAT(o.order_date, '{格式}') AS period,
       SUM(o.total_amount) AS total_sales,
       COUNT(*) AS order_count
FROM orders o
WHERE o.status = '已完成'
  AND o.order_date >= '{起始日期}'
  AND o.order_date < '{结束日期}'
GROUP BY period
ORDER BY period
```

| 粒度 | 格式 | 说明 |
|------|------|------|
| 按天 | `'%Y-%m-%d'` | 适用于短期趋势 |
| 按月 | `'%Y-%m'` | 适用于中期趋势 |
| 按年 | `'%Y'` | 适用于长期趋势 |

### 2.3 TOP-N 排行

```sql
SELECT u.username, SUM(o.total_amount) AS total_spent
FROM orders o
JOIN users u ON o.user_id = u.user_id
WHERE o.status = '已完成'
GROUP BY u.user_id
ORDER BY total_spent DESC
LIMIT 10
```

### 2.4 对比分析

```sql
SELECT
  CASE
    WHEN o.total_amount >= 1000 THEN '高消费'
    WHEN o.total_amount >= 500 THEN '中消费'
    ELSE '低消费'
  END AS consumption_level,
  COUNT(*) AS order_count,
  AVG(o.total_amount) AS avg_amount
FROM orders o
WHERE o.status = '已完成'
GROUP BY consumption_level
ORDER BY avg_amount DESC
```

### 2.5 占比计算

```sql
SELECT p.category,
       SUM(o.total_amount) AS sales,
       ROUND(SUM(o.total_amount) / (
         SELECT SUM(total_amount) FROM orders WHERE status = '已完成'
       ) * 100, 2) AS pct
FROM orders o
JOIN products p ON o.product_id = p.product_id
WHERE o.status = '已完成'
GROUP BY p.category
ORDER BY sales DESC
```

---

## 3. 错误排查指南

当 SQL 执行报错时，按优先级排查：

| 错误类型 | 常见原因 | 解决方案 |
|----------|---------|----------|
| `Table 'xxx' doesn't exist` | 表名写错，或该表不在当前数据库中 | 检查 INFORMATION_SCHEMA 中的真实表名 |
| `Unknown column 'xxx'` | 字段名写错，或字段属于另一张表 | 检查字段所在表，加上表别名前缀 |
| `You have an error in your SQL syntax` | 语法错误，如引号使用不当 | 检查表名是否用了单引号 |
| `GROUP BY clause` 错误 | MySQL 5.7 ONLY_FULL_GROUP_BY 模式 | 确保 SELECT 中非聚合字段都在 GROUP BY 中 |
| `function xxxx does not exist` | 用错了数据库特有的函数 | MySQL 用 DATE_FORMAT，SQLite 用 strftime |
| 结果为空 | WHERE 条件值不匹配 | 用 SELECT DISTINCT 确认字段实际值 |
