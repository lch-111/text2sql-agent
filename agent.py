"""
==============================================================================
Text-to-SQL Agent 核心 — 兼容层 + 新架构集成入口
==============================================================================
设计思路：
  本文件是保留向后兼容性的适配层。
  - 原有的 TextToSQLAgent 类保留，但其 run() 方法委托给 graph.execute()
  - 原有的 get_agent() 单例保留
  - 原有的 TerminologyManager、FewShotManager 等工具类保留
  - 原有的 SYNONYM_MAP、fix_sql_quoting() 保留

  新架构的核心逻辑在 agents/ 和 graph.py 中。
  详情参见 graph.py 中的 LangGraph 状态图编排。
==============================================================================
"""

import json
import logging
import os
import re
from typing import Optional, Dict, Any, List, Tuple

import pandas as pd
from core.config import CONFIG
logger = logging.getLogger("agent")

SQL_GENERATION_PROMPT_TEMPLATE = """
你是专业的数据库智能分析助手（类似 GitHub 的 /database/SQLBot）。请将用户的自然语言问题转化为精准、高效且安全的 SQL 查询语句，并基于查询结果提供清晰的数据洞察。

## 可用工具与资源
1. 【数据库表结构】下方提供了相关表的 schema 信息（表名、字段名、字段注释）
2. 【业务术语映射】下方提供了业务指标到 SQL 的转换规则
3. 【SQL 执行器】仅拥有 SELECT 权限

## 核心工作流程

### 步骤1：理解用户需求与意图消歧
在生成 SQL 前，判断用户意图是否清晰：
- 如果用户使用模糊词汇（如"最近"、"业绩大涨"、"高价值客户"），且无法通过上下文明确其具体含义，**必须先输出以下 JSON 让用户澄清**，严禁自行猜测：
  {{"clarification_needed": true, "question": "你的澄清问题，例如：请问您指的'最近'是过去7天、30天还是本季度？"}}
  只输出这个 JSON，不要输出 SQL
- 如果意图清晰，继续下一步

### 步骤2：分析表结构
- 检查下方【数据库表结构】找到与问题最相关的表和字段
- 严禁编造下方未列出的表名或字段名
- 如果涉及多表关联，确定表之间的关联字段

### 步骤3：确定查询条件
- 过滤条件是什么？
- 是否需要聚合（SUM/COUNT/AVG等）？
- 是否需要分组、排序、限制条数？
- 如果问题涉及时间但未指定范围，自动加上近一年的过滤条件（当前日期 {current_date}）

### 步骤4：编写 SQL
先输出逐步分析过程，然后用 ```sql``` 包裹最终 SQL。必须包含 ```sql``` 标记以便提取 SQL。

## 核心约束
1. 只生成 SELECT 查询，禁止 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE
2. 除非用户明确要求全量数据，否则必须添加 LIMIT 50 防止全表扫描
3. 涉及多表关联时，必须使用表别名（如 orders o, users u）
4. {db_syntax_guide}
5. 严禁编造数据库中不存在的表名或字段名

## 结果输出要求
- 获取执行结果后，用简洁的自然语言总结数据结论
- 如果结果为空，分析可能原因（筛选条件过严、时间范围无数据等）并给出优化建议

【业务术语 → SQL 映射】
{synonym_map}

【数据库表结构（含列注释）】
{schema_str}

【参考示例】
{few_shot_examples}

【用户问题】
{user_question}
"""

SQL_CORRECTION_PROMPT_TEMPLATE = """
你是专业的数据库智能分析助手。上一条 SQL 执行时出错，请根据表结构和错误信息修正 SQL 语句。

【数据库类型】
当前数据库: {db_type}

【数据库表结构】
{schema_str}

【用户问题】
{user_question}

【错误的 SQL】
{wrong_sql}

【错误信息】
{error_message}

{recovery_strategy}

## 修正要求
1. 使用当前数据库类型对应的 SQL 语法，不要混用不同数据库的语法
2. 只输出修正后的 SQL，用 ```sql``` 包裹，不要输出任何解释文字
3. 确保只包含 SELECT 查询
4. 检查表名和字段名是否真实存在，不要编造
"""

SEMANTIC_PARSE_PROMPT = """
你是数据库查询意图分析专家。根据用户问题和数据库 Schema，精确解析查询意图。

【数据库 Schema（JSON）】
{schema_json}

【字段实际值参考（filter.value 必须从此列表中选择）】
{field_values}

【术语同义词映射（用户口语→数据库实际值）】
{term_hints}

【用户问题】
{user_question}

请分析用户意图并输出以下 JSON（只输出 JSON，不要输出其他内容）：
{{
    "intent": "aggregation|filter|trend|list|count",
    "primary_table": "主查询表名",
    "related_tables": ["关联表名"],
    "filters": [
        {{"field": "字段名", "op": "=", "value": "必须从字段实际值参考中选择，不得使用用户原词"}}
    ],
    "time_range": {{
        "field": "日期字段名",
        "start": "YYYY-MM-DD 或 null",
        "end": "YYYY-MM-DD 或 null",
        "natural_desc": "原文时间描述"
    }},
    "aggregation": {{"func": "SUM|COUNT|AVG|MAX|MIN|null", "field": "字段名", "alias": "别名"}},
    "group_by": ["分组字段"],
    "order_by": {{"field": "排序字段", "direction": "ASC|DESC"}},
    "limit": 50,
    "clarification_needed": false,
    "missing_info": []
}}

规则（必须严格执行）：
1. 【表名】只能从【数据库 Schema（JSON）】的顶层 key 中选择真实存在的表
2. 【字段】只能使用所选表中真实存在的字段名
3. 【值】filter.value 必须从【字段实际值参考】中选取语义最匹配的值（允许近似匹配，如“广栋”→“广东”），若无法匹配则 clarification_needed=true
4. 用户口语如"广东省"但实际存"广东"，用映射后的值
5. 不在字段值列表也不在术语映射中时，clarification_needed=true
6. 【禁止编造】严禁使用 schema 中不存在的表名、字段名、字段值
7. 时间口语自动换算，模糊时 clarification_needed=true
"""

SQL_FROM_INTENT_PROMPT = """
你是 SQL 生成专家。根据语义解析意图和数据库 Schema 生成精确的 SELECT 语句。

【数据库类型】
{db_type}

【数据库 Schema（仅以下表可用）】
{schema_str}

【字段匹配提示】
根据【数据库 Schema】中列出的真实字段名，结合字段注释和字段实际值，推断用户口语对应的字段。
例：用户说"销售额"→ Schema中有 `amount` 字段且注释为"订单金额"→ 使用 `amount`。
严禁使用 Schema 中不存在的列名。

【语义解析结果】
{parsed_intent_json}

【用户原始问题】
{user_question}

## 核心约束（必须严格遵守）
1. 【表名】FROM/JOIN 只允许使用【可用数据库表】中列出的表名，禁止任何未列出的表
2. 【字段】SELECT/WHERE/GROUP BY/ORDER BY 只允许使用表中实际存在的字段
3. 【值】WHERE 条件中的文本值必须使用【字段实际值参考】中列出的值
6. 多表关联时根据同名字段自动 JOIN
7. LIMIT {limit}
8. 按 parsed_intent_json 中的 filters/group_by/order_by 生成 SQL
【引号强制规则】
- 表名、列名、别名：一律**不允许使用单引号**。它们要么不加引号，要么使用反引号 `` ` ``。
- 字符串值（WHERE 条件中的常量）：必须使用单引号 `'`。
- 错误示例（禁止）：JOIN 'sales_order' o，'o'.product_number
- 正确示例：JOIN sales_order o 或 JOIN `sales_order` o，o.product_number
"""

# ============================================================================
# 向后兼容：关键工具函数和类
# ============================================================================

# 从 agents/generator_agent.py 导入（避免重复定义）
from agents.generator_agent import fix_sql_quoting


# ============================================================================
# 向后兼容：FewShotManager
# ============================================================================

class FewShotManager:
    """
    Few-Shot 示例管理（向后兼容）。

    与原有功能完全一致，详情参见 agents/schema_retriever.py。
    """

    def __init__(self):
        self._examples = [
            {
                "question": "查询每个省份的订单总金额",
                "sql": "SELECT province, SUM(amount) AS total_sales FROM orders GROUP BY province ORDER BY total_sales DESC",
                "scenario": "aggregation_groupby",
            },
            {
                "question": "查询消费金额最高的前10名用户",
                "sql": "SELECT username, city, SUM(amount) AS total_spent FROM orders WHERE status = '已完成' GROUP BY user_id ORDER BY total_spent DESC LIMIT 10",
                "scenario": "orderby_limit",
            },
            {
                "question": "查询上个月的销售额",
                "sql": "SELECT DATE_FORMAT(order_date, '%Y-%m-%d') AS day, SUM(amount) AS daily_sales FROM orders WHERE status = '已完成' AND order_date >= DATE_SUB(NOW(), INTERVAL 1 MONTH) GROUP BY day ORDER BY day",
                "scenario": "time_range",
            },
            {
                "question": "统计广东地区购买最多的5种商品",
                "sql": "SELECT p.product_name, SUM(o.quantity) AS total_sold FROM orders o JOIN users u ON o.user_id = u.user_id JOIN products p ON o.product_id = p.product_id WHERE u.province = '广东' AND o.status = '已完成' GROUP BY p.product_id ORDER BY total_sold DESC LIMIT 5",
                "scenario": "multi_join",
            },
            {
                "question": "帮我查一下数据",
                "sql": None,
                "scenario": "clarification",
                "clarification": "请问您想查询哪个表的数据？请提供具体的查询条件。",
            },
        ]

    def get_all_examples(self) -> str:
        lines = []
        for ex in self._examples:
            if ex["sql"] is None:
                continue
            lines.append(f"- 问题: {ex['question']}")
            lines.append(f"  SQL: {ex['sql']}")
        return "\n".join(lines)

    def retrieve_similar(self, question: str, k: int = None) -> str:
        if k is None:
            from core.config import CONFIG
            k = CONFIG.agent.few_shot_count
        if not CONFIG.agent.enable_few_shot or k <= 0:
            return ""

        def calc_similarity(q1: str, q2: str) -> float:
            q1_tokens = set(re.findall(r'[一-鿿]+|[a-zA-Z_]+', q1.lower()))
            q2_tokens = set(re.findall(r'[一-鿿]+|[a-zA-Z_]+', q2.lower()))
            if not q1_tokens or not q2_tokens:
                return 0.0
            return len(q1_tokens & q2_tokens) / len(q1_tokens | q2_tokens)

        scored = []
        for ex in self._examples:
            if ex["sql"] is None:
                continue
            sim = calc_similarity(question, ex["question"])
            scored.append((sim, ex))
        scored.sort(key=lambda x: x[0], reverse=True)
        top_k = scored[:k]
        lines = []
        for sim, ex in top_k:
            lines.append(f"问题: {ex['question']}")
            lines.append(f"SQL: {ex['sql']}")
        return "\n" + "\n".join(lines)


# ============================================================================
# 向后兼容：SQLValidator
# ============================================================================

class SQLValidator:
    """
    SQL 安全校验器（向后兼容）。

    新架构中安全的物理拦截由 SQLGuard 负责。
    本类保留仅作 SELECT 校验基础检查。
    """

    DANGEROUS_PATTERNS = [
        r'\bINSERT\b', r'\bUPDATE\b', r'\bDELETE\b', r'\bDROP\b',
        r'\bALTER\b', r'\bCREATE\b', r'\bTRUNCATE\b', r'\bEXEC\b',
        r'\bEXECUTE\b', r'\bATTACH\b', r'\bDETACH\b', r'\bREINDEX\b',
        r'\bREPLACE\b',
    ]

    @classmethod
    def validate(cls, sql: str) -> Tuple[bool, str]:
        sql_upper = sql.strip().upper()
        for keyword in ("SELECT", "WITH"):
            idx = sql_upper.find(keyword)
            if idx >= 0:
                sql_upper = sql_upper[idx:]
                break
        else:
            return False, "只允许 SELECT 查询语句"
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, sql_upper):
                return False, f"SQL 包含禁止的操作: {pattern}"
        return True, ""


# ============================================================================
# 向后兼容：TerminologyManager
# ============================================================================

class TerminologyManager:
    """
    术语管理（向后兼容）。

    新架构中 SchemaRetriever 承担此职责。
    保留本类供外部代码引用。
    """

    def __init__(self, path: str = None):
        if path is None:
            path = os.path.join(os.path.dirname(__file__), "data", "term_mapping.json")
        self.synonyms: Dict[str, str] = {}
        self.stop_words: List[str] = []
        self._load(path)

    def _load(self, path: str):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.synonyms = data.get("synonyms", {})
            self.stop_words = data.get("stop_words", [])
        except Exception as e:
            logger.warning(f"[TerminologyManager] 加载失败: {e}")

    def translate_value(self, value: str) -> str:
        if not value:
            return value
        if value in self.synonyms:
            return self.synonyms[value]
        cleaned = self.clean_query(value)
        if cleaned != value and cleaned in self.synonyms:
            return self.synonyms[cleaned]
        return value

    def clean_query(self, text: str) -> str:
        for w in self.stop_words:
            text = text.replace(w, "")
        return text.strip()


# ============================================================================
# 向后兼容：load_term_mappings
# ============================================================================

def load_term_mappings(path: str = None) -> Dict:
    """从 JSON 加载术语映射（向后兼容，已由 SchemaRetriever 替代）"""
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"_deprecated": True, "_message": "已由 SchemaRetriever.FieldResolver 自动完成映射"}


# ============================================================================
# TextToSQLAgent — 兼容层，内部委托给新架构
# ============================================================================

class TextToSQLAgent:
    """
    Text-to-SQL Agent 核心类（向后兼容层）。

    保持原有的 run()、generate_sql()、generate_and_execute() 接口，
    内部委托给 graph.py 中的 LangGraph 编排。

    改造前：
      agent.py 中的单体类管理所有逻辑
    改造后：
      TextToSQLAgent 是外观（Facade），核心逻辑在 agents/ 和 graph.py 中
    """

    def __init__(self):
        self.config = CONFIG.agent
        self.few_shot = FewShotManager()
        self.validator = SQLValidator()

        # 保留原有引用以便外部代码调用
        self.term = TerminologyManager()
        self.term_map = load_term_mappings()

        # LangSmith 追踪（兼容）
        try:
            from tracing import init_tracing, get_recorder, is_tracing_enabled
            init_tracing()
            self.trace_recorder = get_recorder() if is_tracing_enabled() else None
        except Exception as e:
            logger.debug(f"[Agent] 追踪模块加载状态: {e}")
            self.trace_recorder = None

        logger.info("[Agent] TextToSQLAgent 兼容层初始化完成 (新架构)")

    # ========================================================================
    # 核心方法
    # ========================================================================

    def run(self, question: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        执行完整的 Text-to-SQL 链路。

        委托给 graph.py 的 LangGraph 状态图编排。
        自动维护多轮对话上下文（conversation_history）。

        参数:
            question: 用户的自然语言问题
            use_cache: 是否启用缓存（兼容参数）

        返回:
            {
                "question": str,
                "sql": str | None,
                "result": list | None,
                "columns": list | None,
                "error": str | None,
                "clarification": str | None,
                "cache_hit": bool,
                "cache_source": str | None,
                "retries": int,
                "execution_time": float,
                "conversation_history": list,  # 多轮对话上下文
            }
        """
        from graph import execute as graph_execute

        # 传入累积的对话历史（支持跨轮追问）
        result = graph_execute(
            question=question,
            conversation_history=getattr(self, '_conv_history', []),
        )

        # 更新实例级别的对话历史
        if result.get("conversation_history"):
            self._conv_history = result["conversation_history"]

        # 如果 use_cache=False，强制跳过缓存
        if not use_cache:
            result["cache_hit"] = False
            result["cache_source"] = None

        return result

    def generate_sql(self, question: str) -> str:
        """
        只生成 SQL，不执行（兼容旧接口）。

        参数:
            question: 用户问题

        返回:
            (sql, token_estimate)
        """
        from graph import execute as graph_execute

        result = graph_execute(question)
        sql = result.get("sql", "")
        token_estimate = len(sql)
        return sql, token_estimate

    def generate_and_execute(self, question: str) -> Dict[str, Any]:
        """
        统一的 Text-to-SQL 入口（兼容旧接口）。

        参数:
            question: 用户问题

        返回:
            同 run() 的返回格式
        """
        return self.run(question=question, use_cache=True)

    # ========================================================================
    # 辅助方法（保留引用）
    # ========================================================================

    def get_schema_summary(self) -> str:
        """获取数据库结构摘要"""
        try:
            from core.database import get_db
            db = get_db()
            info_list = db.get_table_info()
            parts = []
            for info in info_list:
                cols = ", ".join(
                    f"{c['name']}({c['type']})" for c in info["columns"]
                )
                parts.append(f"{info['table_name']}({cols})")
            return " | ".join(parts)
        except Exception as e:
            return f"获取schema失败: {e}"

    def _extract_sql(self, text: str) -> str:
        """SQL 提取（委托给 GeneratorAgent）"""
        from agents.generator_agent import GeneratorAgent
        return GeneratorAgent._extract_sql_static(text)

    def normalize_user_query(self, query: str) -> str:
        """查询标准化（委托给 SchemaRetriever）"""
        try:
            from agents.schema_retriever import SchemaRetriever
            retriever = SchemaRetriever()
            return retriever.normalize_query(query)
        except Exception:
            return query


# ============================================================================
# 向后兼容扩展：GeneratorAgent._extract_sql_static
# ============================================================================

# 在 GeneratorAgent 类上添加静态方法
def _extract_sql_static(text: str) -> str:
    """静态 SQL 提取方法"""
    if not text:
        return ""
    text = text.strip()
    sql_block = re.search(
        r"```sql\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE
    )
    if not sql_block:
        all_blocks = list(
            re.finditer(r"```\s*\n?(.*?)\n?```", text, re.DOTALL)
        )
        if all_blocks:
            sql_block = all_blocks[-1]
    if sql_block:
        extracted = sql_block.group(1).strip()
        match = re.search(r"(SELECT|WITH)\s", extracted, re.IGNORECASE)
        if match:
            extracted = extracted[match.start():]
        return extracted
    return text


from agents.generator_agent import GeneratorAgent as _GeneratorAgent
_GeneratorAgent._extract_sql_static = staticmethod(_extract_sql_static)


# ============================================================================
# 全局单例
# ============================================================================

_agent_instance: Optional[TextToSQLAgent] = None


def get_agent() -> TextToSQLAgent:
    """获取 Agent 单例"""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = TextToSQLAgent()
    return _agent_instance


# ============================================================================
# 独立测试入口
# ============================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("Text-to-SQL Agent 测试（新架构）")
    print("=" * 60)

    agent = get_agent()

    test_questions = [
        "统计每个省份的订单总金额",
        "查询消费最多的前5名用户",
    ]

    for q in test_questions:
        print(f"\n{'─' * 60}")
        print(f"问题: {q}")
        print(f"{'─' * 60}")
        try:
            result = agent.run(q, use_cache=False)
            print(f"SQL: {result['sql']}")
            print(f"行数: {len(result.get('result', []) or [])}")
            print(f"缓存命中: {result['cache_hit']}")
            if result['error']:
                print(f"错误: {result['error']}")
            if result.get('result'):
                for row in result['result'][:3]:
                    print(f"  {row}")
        except Exception as e:
            print(f"执行失败: {e}")
            import traceback
            traceback.print_exc()
