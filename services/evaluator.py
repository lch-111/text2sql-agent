"""
==============================================================================
自动化评估流水线 — 动态评估数据集 + 量化指标 —— 【核心亮点】
==============================================================================
设计思路：
  在 Text-to-SQL 系统中，评估至关重要——你不能"感觉"它工作得好，
  而要"证明"它工作得好。

  本模块实现:
  1. 动态评估数据集：评估启动时基于「当前连接库」自动生成用例（含标准 SQL
     答案），由 services/eval_dataset_builder.py 用模板启发式完成；不再硬编码
     依赖某张业务表，切换任意数据库都能跑出有意义、可执行的评估。
  2. 评估指标：
     - 执行准确率 (Execution Accuracy)：生成的 SQL 执行结果与标准 SQL 是否一致
     - SQL 语法正确率：生成的 SQL 是否能被数据库正常执行
     - 忠实度 (Faithfulness)：生成的 SQL 是否忠实于用户问题意图
  3. 评估报告：结构化输出到 JSON 文件，用于后续展示

  评估流程:
    对评估数据集中的每一条:
      问题 → Agent 生成 SQL → 执行 → 与标准 SQL 结果比对 → 打分
==============================================================================
"""

import json
import logging
import os
import re
import time
import traceback
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any
from datetime import datetime

import pandas as pd

from core.config import CONFIG
from core.database import get_db
from core.agent import TextToSQLAgent

logger = logging.getLogger("evaluator")


# ============================================================================
# Golden Dataset 定义
# ============================================================================

GOLDEN_DATASET = [
    # ---- 基础查询 ----
    {
        "id": "Q001",
        "category": "基础聚合",
        "question": "总共有多少用户",
        "standard_sql": "SELECT COUNT(*) AS total_users FROM users",
        "description": "简单计数",
    },
    {
        "id": "Q002",
        "category": "基础聚合",
        "question": "统计各个省份的用户数量",
        "standard_sql": "SELECT province, COUNT(*) AS user_count FROM users GROUP BY province ORDER BY user_count DESC",
        "description": "分组聚合",
    },
    {
        "id": "Q003",
        "category": "基础聚合",
        "question": "查询所有商品的平均价格",
        "standard_sql": "SELECT AVG(price) AS avg_price FROM products",
        "description": "平均值计算",
    },
    # ---- 多表 JOIN ----
    {
        "id": "Q004",
        "category": "多表关联",
        "question": "统计每个省份的订单总金额",
        "standard_sql": "SELECT u.province, SUM(o.total_amount) AS total_sales FROM orders o JOIN users u ON o.user_id = u.user_id WHERE o.status = '已完成' GROUP BY u.province ORDER BY total_sales DESC",
        "description": "两表 JOIN + 聚合 + 过滤",
    },
    {
        "id": "Q005",
        "category": "多表关联",
        "question": "查询各个商品类别的销售数量",
        "standard_sql": "SELECT p.category, SUM(o.quantity) AS total_quantity FROM orders o JOIN products p ON o.product_id = p.product_id WHERE o.status = '已完成' GROUP BY p.category ORDER BY total_quantity DESC",
        "description": "两表 JOIN + 聚合",
    },
    {
        "id": "Q006",
        "category": "多表关联",
        "question": "查询消费金额最高的前5名用户",
        "standard_sql": "SELECT u.username, u.city, u.vip_level, SUM(o.total_amount) AS total_spent FROM orders o JOIN users u ON o.user_id = u.user_id WHERE o.status = '已完成' GROUP BY o.user_id ORDER BY total_spent DESC LIMIT 5",
        "description": "多表 JOIN + 聚合 + 排序 + LIMIT",
    },
    # ---- 时间分析 ----
    {
        "id": "Q007",
        "category": "时间分析",
        "question": "统计每个月的订单数量",
        "standard_sql": "SELECT strftime('%Y-%m', order_date) AS month, COUNT(*) AS order_count FROM orders GROUP BY month ORDER BY month",
        "description": "时间格式化 + 分组",
    },
    {
        "id": "Q008",
        "category": "时间分析",
        "question": "查询每个月的销售额",
        "standard_sql": "SELECT strftime('%Y-%m', order_date) AS month, SUM(total_amount) AS monthly_sales FROM orders WHERE status = '已完成' GROUP BY month ORDER BY month",
        "description": "月度销售趋势",
    },
    {
        "id": "Q009",
        "category": "时间分析",
        "question": "2024年第四季度每个月的销售总额",
        "standard_sql": "SELECT strftime('%m', order_date) AS month, SUM(total_amount) AS quarterly_sales FROM orders WHERE order_date >= '2024-10-01' AND order_date < '2025-01-01' AND status = '已完成' GROUP BY month ORDER BY month",
        "description": "指定季度 + 分组聚合",
    },
    # ---- 条件过滤 ----
    {
        "id": "Q010",
        "category": "条件过滤",
        "question": "查询广东地区的所有已完成订单",
        "standard_sql": "SELECT o.* FROM orders o JOIN users u ON o.user_id = u.user_id WHERE u.province = '广东' AND o.status = '已完成'",
        "description": "跨表条件过滤",
    },
    {
        "id": "Q011",
        "category": "条件过滤",
        "question": "查询价格高于500元的商品有哪些",
        "standard_sql": "SELECT product_id, product_name, category, price FROM products WHERE price > 500 ORDER BY price DESC",
        "description": "数值范围过滤",
    },
    {
        "id": "Q012",
        "category": "条件过滤",
        "question": "查询银卡会员以上的用户（黄金和钻石会员）",
        "standard_sql": "SELECT user_id, username, vip_level FROM users WHERE vip_level IN ('黄金会员', '钻石会员') ORDER BY vip_level",
        "description": "枚举值过滤",
    },
    # ---- 高级分析 ----
    {
        "id": "Q013",
        "category": "高级分析",
        "question": "不同会员等级的平均客单价",
        "standard_sql": "SELECT u.vip_level, AVG(o.total_amount) AS avg_order_amount FROM orders o JOIN users u ON o.user_id = u.user_id GROUP BY u.vip_level ORDER BY avg_order_amount DESC",
        "description": "多表 JOIN + 分组 + 平均值",
    },
    {
        "id": "Q014",
        "category": "高级分析",
        "question": "查询每个商品的毛利率并降序排列",
        "standard_sql": "SELECT product_name, price, cost, ROUND((price - cost) / price * 100, 2) AS margin_rate FROM products ORDER BY margin_rate DESC",
        "description": "字段计算 + 排序",
    },
    {
        "id": "Q015",
        "category": "高级分析",
        "question": "查询广东地区销量最高的前3种商品",
        "standard_sql": "SELECT p.product_name, SUM(o.quantity) AS total_sold FROM orders o JOIN users u ON o.user_id = u.user_id JOIN products p ON o.product_id = p.product_id WHERE u.province = '广东' AND o.status = '已完成' GROUP BY o.product_id ORDER BY total_sold DESC LIMIT 3",
        "description": "三表 JOIN + 过滤 + 聚合 + LIMIT",
    },
    {
        "id": "Q016",
        "category": "高级分析",
        "question": "统计每个城市的订单完成率（完成订单占比）",
        "standard_sql": "SELECT u.city, COUNT(*) AS total_orders, SUM(CASE WHEN o.status = '已完成' THEN 1 ELSE 0 END) AS completed_orders, ROUND(100.0 * SUM(CASE WHEN o.status = '已完成' THEN 1 ELSE 0 END) / COUNT(*), 2) AS completion_rate FROM orders o JOIN users u ON o.user_id = u.user_id GROUP BY u.city ORDER BY completion_rate DESC",
        "description": "CASE WHEN 条件统计 + 分组",
    },
    {
        "id": "Q017",
        "category": "高级分析",
        "question": "每日销售额趋势",
        "standard_sql": "SELECT order_date, SUM(total_amount) AS daily_sales, COUNT(*) AS order_count FROM orders WHERE status = '已完成' GROUP BY order_date ORDER BY order_date",
        "description": "按天聚合趋势分析",
    },
    # ---- 排序与TOP-N ----
    {
        "id": "Q018",
        "category": "排序分析",
        "question": "查询库存最多的前10个商品",
        "standard_sql": "SELECT product_id, product_name, category, stock FROM products ORDER BY stock DESC LIMIT 10",
        "description": "简单排序 + LIMIT",
    },
    {
        "id": "Q019",
        "category": "排序分析",
        "question": "查询销量最低的5个商品",
        "standard_sql": "SELECT p.product_id, p.product_name, p.category, COALESCE(SUM(o.quantity), 0) AS total_sold FROM products p LEFT JOIN orders o ON p.product_id = o.product_id AND o.status = '已完成' GROUP BY p.product_id ORDER BY total_sold ASC LIMIT 5",
        "description": "LEFT JOIN + 聚合 + 升序排列",
    },
    # ---- 复杂分析 ----
    {
        "id": "Q020",
        "category": "复杂分析",
        "question": "统计各年龄段的用户分布（20岁以下，20-30，30-40，40-50，50岁以上）",
        "standard_sql": "SELECT CASE WHEN age < 20 THEN '20岁以下' WHEN age BETWEEN 20 AND 29 THEN '20-29岁' WHEN age BETWEEN 30 AND 39 THEN '30-39岁' WHEN age BETWEEN 40 AND 49 THEN '40-49岁' ELSE '50岁及以上' END AS age_group, COUNT(*) AS user_count FROM users GROUP BY age_group ORDER BY age_group",
        "description": "CASE WHEN 分桶统计",
    },
    {
        "id": "Q021",
        "category": "复杂分析",
        "question": "查询同时购买了电子产品与图书的用户",
        "standard_sql": "SELECT u.user_id, u.username FROM users u WHERE u.user_id IN (SELECT DISTINCT o.user_id FROM orders o JOIN products p ON o.product_id = p.product_id WHERE p.category = '电子产品') AND u.user_id IN (SELECT DISTINCT o.user_id FROM orders o JOIN products p ON o.product_id = p.product_id WHERE p.category = '图书文具')",
        "description": "子查询 + 多类别交叉用户",
    },
    {
        "id": "Q022",
        "category": "复杂分析",
        "question": "查询比平均价格高的商品",
        "standard_sql": "SELECT product_id, product_name, category, price FROM products WHERE price > (SELECT AVG(price) FROM products) ORDER BY price DESC",
        "description": "子查询 + 比较",
    },
    {
        "id": "Q023",
        "category": "高级分析",
        "question": "统计各级别会员的用户数量和平均消费金额",
        "standard_sql": "SELECT u.vip_level, COUNT(DISTINCT u.user_id) AS user_count, AVG(o.total_amount) AS avg_order_amount FROM users u LEFT JOIN orders o ON u.user_id = o.user_id GROUP BY u.vip_level ORDER BY avg_order_amount DESC",
        "description": "LEFT JOIN + 双聚合（计数+均值）",
    },
    {
        "id": "Q024",
        "category": "时间分析",
        "question": "查询2024年每月销售额相比于上月的增长情况",
        "standard_sql": "WITH monthly AS (SELECT strftime('%Y-%m', order_date) AS month, SUM(total_amount) AS sales FROM orders WHERE status = '已完成' AND order_date >= '2024-01-01' AND order_date < '2025-01-01' GROUP BY month) SELECT month, sales, LAG(sales) OVER (ORDER BY month) AS prev_sales, ROUND((sales - LAG(sales) OVER (ORDER BY month)) / LAG(sales) OVER (ORDER BY month) * 100, 2) AS growth_rate FROM monthly ORDER BY month",
        "description": "CTE + 窗口函数 LAG + 环比增长率",
    },
]


# ============================================================================
# 评估指标计算
# ============================================================================

class Evaluator:
    """
    自动化评估器。

    指标定义:
    - Execution Accuracy (EA)：生成的 SQL 执行结果与标准 SQL 结果是否一致
      EA = 结果一致的用例数 / 总用例数
    - SQL Syntax Validity (SV)：生成的 SQL 是否能被数据库正常执行
      SV = 语法正确的用例数 / 总用例数
    - Cache Hit Rate (CH)：缓存在评估期间的综合命中率
      从缓存系统统计获取
    """

    def __init__(self, dataset: List[Dict] = None, db=None):
        """
        初始化评估器。

        参数:
            dataset: 评估用例列表。为 None 时自动基于「当前连接库」动态生成
                     （见 services/eval_dataset_builder.py，不再依赖硬编码黄金数据集，
                       因此切换任意数据库都能评估，不会出现标准 SQL 引用不存在表而全 0）
            db: 数据库管理器实例（默认取全局单例）

        说明:
            动态构建仅做只读 introspection + SELECT 采样/自校验，不改库、不落盘。
        """
        self.db = db or get_db()
        self.dataset = dataset
        if self.dataset is None:
            self.dataset = self._build_dataset()
        self.results: List[Dict] = []

    def _build_dataset(self) -> List[Dict]:
        """惰性导入构造器并动态生成评估用例（延迟 import 避免顶层循环依赖）"""
        from services.eval_dataset_builder import build_eval_dataset

        snapshot = build_eval_dataset(self.db)
        if snapshot.skips:
            for s in snapshot.skips[:20]:
                logger.info("评估数据集跳过: %s | %s", s.get("category", ""), s.get("reason", ""))
        return snapshot.cases

    def _compare_results(self, pred_df: pd.DataFrame, std_df: pd.DataFrame) -> Dict:
        """
        比较预测结果与标准结果。

        使用多维度比对策略:
        1. 行数是否一致
        2. 列名集合是否一致（忽略大小写和顺序）
        3. 数据内容是否一致（排序后逐行比较数值）
        """
        report = {
            "row_count_match": False,
            "columns_match": False,
            "data_match": False,
            "match_score": 0.0,
        }

        if pred_df is None or std_df is None:
            return report

        try:
            # 行数比对
            pred_rows = len(pred_df)
            std_rows = len(std_df)
            report["row_count_match"] = pred_rows == std_rows
            report["pred_rows"] = pred_rows
            report["std_rows"] = std_rows

            # 列名比对
            pred_cols = set(c.lower() for c in pred_df.columns)
            std_cols = set(c.lower() for c in std_df.columns)
            report["columns_match"] = pred_cols == std_cols

            # 数据比对（对数值列）
            if report["row_count_match"]:
                try:
                    # 对齐列名
                    common_cols = list(set(pred_df.columns) & set(std_df.columns))
                    if common_cols:
                        pred_sorted = pred_df[common_cols].sort_values(
                            by=common_cols[0]
                        ).reset_index(drop=True)
                        std_sorted = std_df[common_cols].sort_values(
                            by=common_cols[0]
                        ).reset_index(drop=True)

                        # 比较数值（容忍浮点误差）
                        match_count = 0
                        total = len(common_cols) * len(pred_sorted)
                        for col in common_cols:
                            for i in range(len(pred_sorted)):
                                pred_val = pred_sorted[col].iloc[i] if i < len(pred_sorted) else None
                                std_val = std_sorted[col].iloc[i] if i < len(std_sorted) else None
                                if pred_val == std_val:
                                    match_count += 1
                                elif isinstance(pred_val, (int, float)) and isinstance(std_val, (int, float)):
                                    if abs(pred_val - std_val) < 0.01:
                                        match_count += 1

                        report["data_match"] = match_count / total > 0.95 if total > 0 else False
                        report["match_score"] = round(match_count / total, 4) if total > 0 else 1.0
                    elif (
                        len(pred_df) == 1
                        and len(std_df) == 1
                        and len(pred_df.columns) == 1
                        and len(std_df.columns) == 1
                    ):
                        # 标量结果兜底：AVG/COUNT 等聚合结果仅 1 行 1 列，生成器与标准
                        # SQL 的列别名几乎不可能相同（如 avg_val vs total），此时列名
                        # 交集为空无法走上面的逐列比对，改按「单元格数值」直接比。
                        pred_val = pred_df.iloc[0, 0]
                        std_val = std_df.iloc[0, 0]
                        if pred_val == std_val:
                            scalar_ok = True
                        elif isinstance(pred_val, (int, float)) and isinstance(std_val, (int, float)):
                            scalar_ok = abs(pred_val - std_val) < 0.01
                        else:
                            scalar_ok = False
                        report["data_match"] = scalar_ok
                        report["match_score"] = 1.0 if scalar_ok else 0.0
                    elif len(pred_df.columns) == len(std_df.columns):
                        # 通用兜底：列名因别名/命名差异而没有交集（如时间分析里
                        # month/cnt 均为派生列）时，只要「行数 + 列数」一致，就按
                        # 行内容多集比较——每格数值量化到 0.01 以吸收浮点误差，
                        # 排序后逐行比对，可正确区分 SUM/AVG/COUNT 等口径差异。
                        def _row_key(row):
                            key = []
                            for v in row:
                                if v is None:
                                    key.append("")
                                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                                    key.append(int(round(float(v) * 100)))
                                else:
                                    key.append(str(v))
                            return tuple(key)

                        pred_rows = sorted(_row_key(r) for r in pred_df.itertuples(index=False, name=None))
                        std_rows = sorted(_row_key(r) for r in std_df.itertuples(index=False, name=None))
                        n = len(pred_rows)
                        row_match = sum(1 for a, b in zip(pred_rows, std_rows) if a == b) if n else 0
                        report["data_match"] = (row_match / n > 0.95) if n else False
                        report["match_score"] = round(row_match / n, 4) if n else 1.0
                except Exception:
                    report["data_match"] = False
                    report["match_score"] = 0.0
            else:
                report["match_score"] = 0.0

        except Exception as e:
            logger.warning(f"结果比对异常: {e}")

        return report

    def _evaluate_single(self, agent: TextToSQLAgent, case: Dict) -> Dict:
        """
        评估单个用例。

        返回详细的评估结果。
        """
        result = {
            "id": case["id"],
            "category": case["category"],
            "question": case["question"],
            "standard_sql": case["standard_sql"],
            "generated_sql": "",
            "sql_valid": False,
            "execution_match": False,
            "error": None,
            "comparison": {},
            "execution_time": 0,
        }

        try:
            # Step 1: 用 Agent 生成并执行 SQL
            agent_result = agent.run(case["question"], use_cache=False)
            result["generated_sql"] = agent_result.get("sql", "")
            result["execution_time"] = agent_result.get("execution_time", 0)

            if agent_result.get("error"):
                result["error"] = agent_result["error"]
                result["sql_valid"] = False
            else:
                result["sql_valid"] = True

            # Step 2: 执行标准 SQL
            try:
                std_df = self.db.query(case["standard_sql"])
            except Exception as e:
                logger.warning(f"标准 SQL 执行失败: {case['standard_sql']}: {e}")
                std_df = pd.DataFrame()

            # Step 3: 比对结果
            pred_df = None
            if result["sql_valid"]:
                try:
                    pred_df = self.db.query(result["generated_sql"])
                except Exception:
                    pass

            comparison = self._compare_results(pred_df, std_df)
            result["comparison"] = comparison
            result["execution_match"] = comparison.get("data_match", False)

        except Exception as e:
            result["error"] = str(e)
            logger.error(f"评估用例 {case['id']} 失败: {e}")

        return result

    def run_evaluation(self, agent: TextToSQLAgent = None) -> List[Dict]:
        """
        运行完整评估。

        参数:
            agent: 如果为 None，则创建新的 Agent 实例

        返回:
            每个用例的详细评估结果列表
        """
        if agent is None:
            agent = TextToSQLAgent()

        print(f"开始评估 {len(self.dataset)} 个用例...\n")

        self.results = []
        for i, case in enumerate(self.dataset, 1):
            print(f"[{i}/{len(self.dataset)}] {case['category']}: {case['question'][:30]}...")
            result = self._evaluate_single(agent, case)
            self.results.append(result)

            status = "✓" if result["sql_valid"] and result["execution_match"] else "✗"
            print(f"  → {status} SQL有效={result['sql_valid']}, 结果匹配={result['execution_match']}")

        return self.results

    def generate_report(self) -> Dict[str, Any]:
        """
        生成量化评估报告。

        报告内容:
        - 总体指标（准确率、语法正确率等）
        - 分类指标（按 Category 分组）
        - 失败用例分析
        """
        if not self.results:
            # 空结果（例如动态数据集在无表/结构不足的库上为 0 条）也必须返回
            # 结构完整、键齐全的报告，避免前端把缺失字段渲染成 NaN/崩溃。
            reason = (
                "当前连接库未生成任何可用评估用例（可能库中无表、无可用数据或表结构不足）。"
                "请确认已连接包含业务表的数据库后重试。"
            )
            return {
                "report_time": datetime.now().isoformat(),
                "dataset_size": 0,
                "message": reason,
                "overall_metrics": {
                    "execution_accuracy": 0.0,
                    "sql_syntax_validity": 0.0,
                    "error_rate": 0.0,
                    "total_valid": 0,
                    "total_match": 0,
                    "total_errors": 0,
                },
                "category_metrics": {},
                "case_details": [],
            }

        total = len(self.results)
        valid_count = sum(1 for r in self.results if r["sql_valid"])
        match_count = sum(1 for r in self.results if r["execution_match"])
        error_count = sum(1 for r in self.results if r.get("error"))

        # 总体指标
        report = {
            "report_time": datetime.now().isoformat(),
            "dataset_size": total,
            "overall_metrics": {
                "execution_accuracy": round(match_count / total * 100, 2),
                "sql_syntax_validity": round(valid_count / total * 100, 2),
                "error_rate": round(error_count / total * 100, 2),
                "total_valid": valid_count,
                "total_match": match_count,
                "total_errors": error_count,
            },
            "category_metrics": {},
            "case_details": [],
        }

        # 分类指标
        categories = {}
        for r in self.results:
            cat = r.get("category", "未分类")
            if cat not in categories:
                categories[cat] = {"total": 0, "valid": 0, "match": 0}
            categories[cat]["total"] += 1
            if r["sql_valid"]:
                categories[cat]["valid"] += 1
            if r["execution_match"]:
                categories[cat]["match"] += 1

        for cat, stats in categories.items():
            report["category_metrics"][cat] = {
                "total": stats["total"],
                "accuracy": round(stats["match"] / stats["total"] * 100, 2),
                "validity": round(stats["valid"] / stats["total"] * 100, 2),
            }

        # 单个用例详情
        for r in self.results:
            case_report = {
                "id": r["id"],
                "category": r["category"],
                "question": r["question"],
                "sql_valid": r["sql_valid"],
                "execution_match": r["execution_match"],
                "generated_sql": r.get("generated_sql", ""),
                "execution_time": round(r.get("execution_time", 0), 3),
            }
            if r.get("error"):
                case_report["error"] = r["error"]
            report["case_details"].append(case_report)

        return report

    def save_report(self, report: Dict = None):
        """保存评估报告到 JSON 文件"""
        if report is None:
            report = self.generate_report()

        output_path = CONFIG.eval.report_path
        # 目录可能不存在（首次运行/容器新卷），先确保父目录可写
        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\n评估报告已保存: {output_path}")
        return output_path

    def print_summary(self, report: Dict = None):
        """打印评估摘要"""
        if report is None:
            report = self.generate_report()

        metrics = report["overall_metrics"]
        print("\n" + "=" * 60)
        print("  📊 评估报告摘要")
        print("=" * 60)
        print(f"  测试用例数: {report['dataset_size']}")
        print(f"  ✅ SQL 语法正确率: {metrics['sql_syntax_validity']}%")
        print(f"  🎯 执行准确率 (Execution Accuracy): {metrics['execution_accuracy']}%")
        print(f"  ❌ 错误率: {metrics['error_rate']}%")
        print(f"\n  分类表现:")
        for cat, cat_metrics in report.get("category_metrics", {}).items():
            print(f"    {cat}: 准确率={cat_metrics['accuracy']}%, "
                  f"语法正确率={cat_metrics['validity']}%")
        print("=" * 60)


# ============================================================================
# 独立测试入口
# ============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)

    evaluator = Evaluator()
    agent = TextToSQLAgent()

    # 运行按当前连接库动态生成的评估用例（数量随 schema 自适应）
    evaluator.run_evaluation(agent)
    report = evaluator.generate_report()
    evaluator.print_summary(report)
    evaluator.save_report(report)
