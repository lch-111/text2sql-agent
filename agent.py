"""
==============================================================================
Text-to-SQL Agent 核心 — 生成、执行与自我修正
==============================================================================
设计思路：
  本模块是系统的核心编排器。整体流程：

  1. 问题分析 → 2. 混合检索 Schema → 3. Few-Shot 注入 → 4. LLM SQL 生成
  → 5. 防注入校验 → 6. SQL 执行 → 7. 错误捕获 → 8. 自我修正（循环）
  → 9. 结果返回 + 写入缓存

  其中步骤 7-8 是自我修正机制：
  当 SQL 执行失败时，将错误信息作为反馈拼入新 Prompt，让 LLM 重写 SQL。
  最多重试 max_retries 次（默认 2 次）。
==============================================================================
"""

import json
import logging
import os
import re
import time
from typing import Optional, Dict, Any, List, Tuple
from datetime import datetime, timedelta

import pandas as pd

from config import CONFIG
from database import get_db

logger = logging.getLogger("agent")

# ============================================================================
# 术语库配置路径
# ============================================================================
_DEFAULT_TERM_PATH = os.path.join(os.path.dirname(__file__), "data", "term_mappings.json")


def load_term_mappings(path: str = None) -> Dict:
    """从 JSON 文件加载术语映射，文件不存在则返回空字典"""
    path = path or _DEFAULT_TERM_PATH
    if not os.path.exists(path):
        logger.warning(f"[术语库] 文件不存在: {path}，使用空映射")
        return {"value_mappings": {}, "field_synonyms": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        vm = data.get("value_mappings", {})
        fs = data.get("field_synonyms", {})
        total_vm = sum(len(v) for v in vm.values())
        total_fs = sum(len(v) for v in fs.values())
        logger.info(f"[术语库] 已加载 {total_vm} 条值映射 + {total_fs} 条字段同义词")
        return data
    except Exception as e:
        logger.warning(f"[术语库] 加载失败: {e}")
        return {"value_mappings": {}, "field_synonyms": {}}


# ============================================================================
# SQL 后处理 — 修正 LLM 常见的标识符引号错误
# ============================================================================
def fix_sql_quoting(sql: str) -> str:
    # 1. 修复 JOIN 后跟单引号表名 + 反引号别名的情况：JOIN 'sales_order' `o` → JOIN `sales_order` `o`
    sql = re.sub(
        r"(?i)(\bJOIN\s+)'(\w+)'\s+`(\w+)`",
        r"\1`\2` \3",
        sql,
    )
    # 2. 修复 JOIN 后跟单引号表名 + 普通别名：JOIN 'sales_order' o → JOIN `sales_order` o
    sql = re.sub(
        r"(?i)(\bJOIN\s+)'(\w+)'\s+(\w+)",
        r"\1`\2` \3",
        sql,
    )
    # 3. 修复 'alias' . column 或 'alias'.`column` 中的别名单引号
    sql = re.sub(
        r"'(\w+)'\s*\.\s*`?(\w+)`?",
        r"`\1`.\2",
        sql,
    )
    # 4. 修复其他位置可能出现的独立单引号标识符（如 ON 条件中）
    sql = re.sub(
        r"(?<!\w)'(\w+)'(?!\w)",
        r"`\1`",
        sql,
    )
    # 5. 保护 WHERE 子句中的字符串值（保留之前逻辑）
    sql = re.sub(
        r"(?i)(=|>|<|>=|<=|!=|IN)\s*`([^`]+)`\s*",
        lambda m: f"{m.group(1)} '{m.group(2)}' " if not m.group(2).isdigit() else m.group(0),
        sql,
    )
    # 6. 终极修复：将 JOIN/FROM/ON/GROUP BY 后残留的单引号标识符转为反引号
    sql = re.sub(
        r"(?i)(\b(?:FROM|JOIN|ON|GROUP\s+BY|ORDER\s+BY|INTO|TABLE)\s+[^'`\s]*?)'(\w+)'(?!\s*=)",
        lambda m: m.group(0).replace(f"'{m.group(2)}'", f"`{m.group(2)}`", 1),
        sql,
    )
    return sql
# ============================================================================
# LLM 接口抽象（支持 Ollama 和 OpenAI）
# ============================================================================

class LLMClient:
    """
    LLM 调用客户端抽象层。

    支持 Ollama（本地部署）和 OpenAI API。
    可根据配置切换 provider。
    """

    def __init__(self):
        self.cfg = CONFIG.llm
        self._client = None
        self._init_client()

    def _init_client(self):
        """根据配置初始化 LLM 客户端"""
        if self.cfg.provider == "ollama":
            try:
                from langchain_ollama import ChatOllama
                self._client = ChatOllama(
                    base_url=self.cfg.ollama_base_url,
                    model=self.cfg.ollama_model,
                    temperature=self.cfg.temperature,
                    max_tokens=self.cfg.max_tokens,
                )
                logger.info(f"[LLM] Ollama 客户端初始化: {self.cfg.ollama_model}")
            except ImportError:
                logger.error("[LLM] langchain-ollama 未安装")
                raise
        elif self.cfg.provider == "openai":
            try:
                from langchain_openai import ChatOpenAI
                kwargs = dict(
                    model=self.cfg.openai_model,
                    temperature=self.cfg.temperature,
                    max_tokens=self.cfg.max_tokens,
                    api_key=self.cfg.openai_api_key,
                )
                if self.cfg.openai_base_url:
                    kwargs["base_url"] = self.cfg.openai_base_url
                self._client = ChatOpenAI(**kwargs)
                logger.info(f"[LLM] OpenAI 客户端初始化: {self.cfg.openai_model}")
            except ImportError:
                logger.error("[LLM] langchain-openai 未安装")
                raise
        else:
            raise ValueError(f"不支持的 LLM provider: {self.cfg.provider}")

    def generate(self, prompt: str) -> str:
        """
        调用 LLM 生成文本。

        参数:
            prompt: 完整的 Prompt 内容

        返回:
            模型生成的文本
        """
        start_time = time.time()
        try:
            response = self._client.invoke(prompt)
            result = response.content if hasattr(response, 'content') else str(response)
            elapsed = time.time() - start_time
            logger.info(f"[LLM] 生成完成 ({elapsed:.2f}s, {len(result)} 字符)")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"[LLM] 生成失败 ({elapsed:.2f}s): {e}")
            raise RuntimeError(f"LLM 调用失败: {e}") from e

    def estimate_tokens(self, text: str) -> int:
        """估算文本的 Token 数（粗略估算: 中文 1.5 token/字，英文 0.4 token/字母）"""
        chinese_chars = len(re.findall(r'[一-鿿]', text))
        english_chars = len(re.findall(r'[a-zA-Z]', text))
        return int(chinese_chars * 1.5 + english_chars * 0.4 + len(text) * 0.1)


# ============================================================================
# 同义词映射
# ============================================================================

SYNONYM_MAP = {
    # 业务指标同义词 → SQL 表达式
    "销量": "SUM(o.quantity)",
    "销售量": "SUM(o.quantity)",
    "销售额": "SUM(o.total_amount)",
    "销售总额": "SUM(o.total_amount)",
    "总收入": "SUM(amount)",
    "总金额": "SUM(amount)",
    "金额": "amount",
    "客单价": "AVG(amount)",
    "订单量": "COUNT(*)",
    "订单数": "COUNT(*)",
    "数量": "quantity",
    "单价": "price",
    "成本": "cost",
    # 时间同义词（MySQL 兼容）
    "今年": "YEAR(NOW())",
    "去年": "YEAR(NOW()) - 1",
    "本月": "DATE_FORMAT(NOW(), '%Y-%m')",
    "上月": "DATE_FORMAT(DATE_SUB(NOW(), INTERVAL 1 MONTH), '%Y-%m')",
    "昨天": "DATE_SUB(CURDATE(), INTERVAL 1 DAY)",
    "本周": "DATE_FORMAT(NOW(), '%Y-%u')",
    # 常用聚合
    "平均值": "AVG",
    "总和": "SUM",
    "最大值": "MAX",
    "最小值": "MIN",
    "计数": "COUNT",
}


def format_synonym_map() -> str:
    """将同义词映射格式化为提示文本"""
    lines = ["业务术语 → SQL 表达式映射："]
    for term, expr in SYNONYM_MAP.items():
        lines.append(f"  「{term}」 → {expr}")
    return "\n".join(lines)


# ============================================================================
# 术语配置模块 — 从 JSON 文件加载同义词映射，不硬编码
# ============================================================================

class TerminologyManager:
    """
    术语管理：加载 term_mapping.json，提供同义词查询和 SQL 值替换。

    用法:
      tm = TerminologyManager()
      tm.translate_value("广东省")  # → "广东"
      tm.clean_query("广东省的销售额")  # → "广东省 销售额"
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
            logger.info(f"[术语] 已加载 {len(self.synonyms)} 条同义词, {len(self.stop_words)} 个停用词")
        except Exception as e:
            logger.warning(f"[术语] 加载失败: {e}")

    def translate_value(self, value: str) -> str:
        """翻译用户口语值到数据库实际存储值"""
        if not value:
            return value
        # 精确匹配
        if value in self.synonyms:
            return self.synonyms[value]
        # 去掉停用词后匹配
        cleaned = self.clean_query(value)
        if cleaned != value and cleaned in self.synonyms:
            return self.synonyms[cleaned]
        return value

    def clean_query(self, text: str) -> str:
        """移除停用词"""
        for w in self.stop_words:
            text = text.replace(w, "")
        return text.strip()

    def reflect_sql(self, sql: str, field_values: Dict[str, List[str]]) -> Tuple[str, bool]:
        """
        检查 SQL 中的字符串值是否匹配数据库实际值，不匹配时修正。
        返回 (修正后的SQL, 是否有修改)
        """
        if not field_values:
            return sql, False

        modified = False
        # 从 SQL 中提取所有字符串值 WHERE col = '值'
        for key, valid_vals in field_values.items():
            table_col = key  # e.g. "orders.province"
            # 对每个有效值，检查 SQL 中是否用了近似的但不精确的值
            for valid in valid_vals:
                # 用户可能输入了带后缀/前缀的版本
                for synonym_input, synonym_target in self.synonyms.items():
                    if synonym_target == valid:
                        pattern = rf"'{re.escape(synonym_input)}'"
                        if re.search(pattern, sql):
                            sql = re.sub(pattern, f"'{valid}'", sql)
                            modified = True
                            logger.info(f"[术语反思] '{synonym_input}' → '{valid}'")
        return sql, modified

    def get_synonym_hints(self) -> str:
        """获取同义词提示文本（给 Prompt 用）"""
        if not self.synonyms:
            return ""
        lines = ["【术语同义词映射（用户口语→数据库存储值）】"]
        for k, v in list(self.synonyms.items())[:30]:
            if k != v:
                lines.append(f"  '{k}' → '{v}'")
        return "\n".join(lines)


# ============================================================================
# Prompt 模板
# ============================================================================

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


# ============================================================================
# 语义解析 Prompt — 通用，不硬编码任何词汇
# ============================================================================

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
3. 【值】filter.value 必须从【字段实际值参考】中选取精确值
4. 用户口语如"广东省"但实际存"广东"，用映射后的值
5. 不在字段值列表也不在术语映射中时，clarification_needed=true
6. 【禁止编造】严禁使用 schema 中不存在的表名、字段名、字段值
7. 时间口语自动换算，模糊时 clarification_needed=true
"""


# ============================================================================
# SQL 生成 Prompt — 接收语义解析结果 + Schema
# ============================================================================

SQL_FROM_INTENT_PROMPT = """
你是 SQL 生成专家。根据语义解析意图和数据库 Schema 生成精确的 SELECT 语句。

【数据库类型】
{db_type}

【数据库 Schema（仅以下表可用）】
{schema_str}

【术语同义词映射】
{term_hints}

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
# 防注入校验器
# ============================================================================

class SQLValidator:
    """
    SQL 安全校验器。

    确保生成的 SQL 只包含 SELECT 查询，不包含危险操作。
    """

    # 禁止出现的 SQL 关键字模式
    DANGEROUS_PATTERNS = [
        r'\bINSERT\b', r'\bUPDATE\b', r'\bDELETE\b', r'\bDROP\b',
        r'\bALTER\b', r'\bCREATE\b', r'\bTRUNCATE\b', r'\bEXEC\b',
        r'\bEXECUTE\b', r'\bATTACH\b', r'\bDETACH\b', r'\bREINDEX\b',
        r'\bREPLACE\b',
    ]

    @classmethod
    def validate(cls, sql: str) -> Tuple[bool, str]:
        """
        校验 SQL 安全性。

        返回:
            (is_valid: bool, error_message: str)
        """
        sql_upper = sql.strip().upper()

        # 自动裁剪到第一个 SELECT/WITH 开头（兼容 LLM 返回的额外前缀文本）
        for keyword in ("SELECT", "WITH"):
            idx = sql_upper.find(keyword)
            if idx >= 0:
                sql_upper = sql_upper[idx:]
                break
        else:
            return False, "只允许 SELECT 查询语句"

        # 检查是否包含危险操作
        for pattern in cls.DANGEROUS_PATTERNS:
            if re.search(pattern, sql_upper):
                return False, f"SQL 包含禁止的操作: {pattern}"

        return True, ""


# ============================================================================
# Few-Shot 示例管理
# ============================================================================

class FewShotManager:
    """
    Few-Shot 示例管理。

    内置 5 个覆盖常见场景的示例，根据用户问题动态检索最相似的。
    """

    def __init__(self):
        # 5 个高质量 Few-Shot 示例（覆盖聚合、排序、时间范围、多表 JOIN、澄清）
        # 使用数据库无关的 SQL 语法，实际执行时根据数据库类型调整
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
                "sql": None,  # 模糊问题 → 需要澄清
                "scenario": "clarification",
                "clarification": "请问您想查询哪个表的数据？请提供具体的查询条件。",
            },
        ]

    def get_all_examples(self) -> str:
        """返回所有示例的格式化文本"""
        lines = []
        for ex in self._examples:
            if ex["sql"] is None:
                continue
            lines.append(f"- 问题: {ex['question']}")
            lines.append(f"  SQL: {ex['sql']}")
        return "\n".join(lines)

    def retrieve_similar(self, question: str, k: int = None) -> str:
        """
        检索与问题最相似的 k 个示例。

        使用简单的基于字符的 TF 相似度进行匹配。
        """
        if k is None:
            k = CONFIG.agent.few_shot_count

        # 如果不需要注入示例
        if not CONFIG.agent.enable_few_shot or k <= 0:
            return ""

        # 使用简单的重叠词数作为相似度
        def calc_similarity(q1: str, q2: str) -> float:
            q1_tokens = set(re.findall(r'[一-鿿]+|[a-zA-Z_]+', q1.lower()))
            q2_tokens = set(re.findall(r'[一-鿿]+|[a-zA-Z_]+', q2.lower()))
            if not q1_tokens or not q2_tokens:
                return 0.0
            intersection = q1_tokens & q2_tokens
            union = q1_tokens | q2_tokens
            return len(intersection) / len(union)

        scored = []
        for ex in self._examples:
            if ex["sql"] is None:
                continue  # 跳过澄清场景（不注入 SQL）
            sim = calc_similarity(question, ex["question"])
            scored.append((sim, ex))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_k = scored[:k]

        lines = []
        for i, (sim, ex) in enumerate(top_k, 1):
            lines.append(f"问题: {ex['question']}")
            lines.append(f"SQL: {ex['sql']}")

        return "\n" + "\n".join(lines)


# ============================================================================
# Text-to-SQL Agent（核心）
# ============================================================================

class TextToSQLAgent:
    """
    Text-to-SQL Agent 核心类。

    工作流程:
    ＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿
    用户问题
        │
        ▼
    ① 查缓存（L1 精确 → L2 语义）──→ 命中 → 直接返回结果
        │ 未命中
        ▼
    ② 混合检索 Schema（BM25 + 向量 + Rerank）
        │
        ▼
    ③ 动态检索 Few-Shot 示例
        │
        ▼
    ④ 构建 Prompt → 调用 LLM 生成 SQL
        │
        ▼
    ⑤ 防注入校验 ──→ 不通过 → 报错
        │ 通过
        ▼
    ⑥ 执行 SQL
        │
        ▼ ◀─── 有错误 ─── ⑦ 自我修正（最多 max_retries 次）
        │ 成功                                 │
        ▼                                     │
    ⑧ 写入缓存 ←─── 修正成功 ────────────┘
        │                               │ 修正失败
        ▼                               ▼
    返回结果                          返回错误
    ＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿＿
    """

    def __init__(self):
        self.config = CONFIG.agent
        self.llm = LLMClient()
        self.validator = SQLValidator()

        # LangSmith 链路追踪
        try:
            from tracing import init_tracing, get_recorder, is_tracing_enabled
            init_tracing()
            self.trace_recorder = get_recorder() if is_tracing_enabled() else None
        except Exception as e:
            logger.debug(f"[Agent] 追踪模块加载状态: {e}")
            self.trace_recorder = None

        self.few_shot = FewShotManager()
        self.db = get_db()
        self.term = TerminologyManager()
        self.term_map = load_term_mappings()
        self.distinct_values: Dict[str, List[str]] = {}
        self._result_cache: Dict[str, Dict] = {}
        self._result_cache_ttl = 3600
        # sqlglot 增强验证器（带有语法解析和表/字段存在性检查）
        try:
            from sql_validator import SQLValidator as EnhancedSQLValidator, ErrorRecoveryChain
            sqlglot_dialect = self._get_sqlglot_dialect()
            self._enhanced_validator = EnhancedSQLValidator(dialect=sqlglot_dialect)
            self._error_recovery = ErrorRecoveryChain()
        except Exception as e:
            logger.warning(f"[Agent] 增强验证器加载失败，使用基础验证: {e}")
            self._enhanced_validator = None
            self._error_recovery = None

        # SQL 优化器（可选）
        try:
            from sql_optimizer import SQLOptimizer
            self.sql_optimizer = SQLOptimizer(enabled=getattr(self.config, 'enable_sql_optimizer', True))
        except Exception:
            self.sql_optimizer = None

    @property
    def hybrid_retriever(self):
        if not hasattr(self, '_hybrid_retriever') or self._hybrid_retriever is None:
            from hybrid_search import HybridRetriever
            self._hybrid_retriever = HybridRetriever()
        return self._hybrid_retriever

    def _get_sqlglot_dialect(self) -> str:
        """根据当前数据库类型返回 sqlglot 方言名称"""
        try:
            db_type = self.db.active_db_type
            mapping = {"mysql": "mysql", "postgres": "postgres", "sqlite": "sqlite"}
            return mapping.get(db_type, "sqlite")
        except Exception:
            return "sqlite"

    # ========================================================================
    # 字段实际值加载 + 用户查询预处理（零硬编码）
    # ========================================================================

    def _load_distinct_values(self) -> Dict[str, List[str]]:
        """
        加载字段实际值 + 术语库映射值，合并到 self.distinct_values。
        返回 { "table.column": ["值1", "值2", ...] }
        """
        # 从数据库加载 DISTINCT 值
        db_vals = self._load_field_values()
        self.distinct_values = dict(db_vals)

        # 补充术语库中的映射值（确保映射目标值也在列表中）
        for field, mappings in self.term_map.get("value_mappings", {}).items():
            for spoken, actual in mappings.items():
                # 找到包含此字段的 key
                for key in list(self.distinct_values.keys()):
                    if key.endswith(f".{field}") or key == field:
                        if actual not in self.distinct_values[key]:
                            self.distinct_values[key].append(actual)
                        break
                else:
                    # 字段还未在 distinct_values 中，创建占位
                    pass  # 可能是新字段，暂时忽略

        logger.info(f"[预处理] 已加载 {sum(len(v) for v in self.distinct_values.values())} 个字段值")
        return self.distinct_values

    def normalize_user_query(self, query: str) -> str:
        """
        预处理用户问题：将口语词替换为数据库实际值。

        遍历 term_map["value_mappings"]，匹配用户问题中的键 → 替换为值。
        完全不硬编码任何词汇，完全靠 term_mappings.json 驱动。
        """
        if not query:
            return query

        result = query
        value_mappings = self.term_map.get("value_mappings", {})

        for field, mappings in value_mappings.items():
            for spoken, actual in mappings.items():
                if spoken in result:
                    result = result.replace(spoken, actual)
                    logger.info(f"[预处理] 术语替换: '{spoken}' → '{actual}'")

        if result != query:
            logger.info(f"[预处理] 标准化前: '{query}' → 后: '{result}'")
        return result

    def _build_term_hints(self) -> str:
        """生成术语映射提示文本（给 Prompt 用）"""
        lines = []
        vm = self.term_map.get("value_mappings", {})
        if vm:
            lines.append("【同义词映射（用户口语→数据库实际值）】")
            for field, mappings in vm.items():
                parts = [f"'{k}'→'{v}'" for k, v in mappings.items()]
                lines.append(f"  {field}: {', '.join(parts)}")
        fs = self.term_map.get("field_synonyms", {})
        if fs:
            lines.append("【业务术语→字段名】")
            for field, synonyms in fs.items():
                lines.append(f"  {field}: {'、'.join(synonyms)}")
        return "\n".join(lines)

    # ========================================================================
    # Schema 自动加载（从 INFORMATION_SCHEMA）
    # ========================================================================

    _db_schema_cache: Optional[Dict] = None
    _db_schema_loaded_at: Optional[float] = None
    _SCHEMA_CACHE_TTL = 300  # 5分钟

    def _load_db_schema(self) -> Dict:
        """
        从 INFORMATION_SCHEMA 加载所有表和字段，缓存到内存。
        返回 {table_name: {columns: [{name, type, comment, nullable, key}], ...}}
        """
        now = time.time()
        if (self._db_schema_cache is not None
                and self._db_schema_loaded_at
                and now - self._db_schema_loaded_at < self._SCHEMA_CACHE_TTL):
            return self._db_schema_cache

        schema = {}
        try:
            engine = self.db.engine
            from sqlalchemy import inspect as sa_inspect, text as sa_text
            inspector = sa_inspect(engine)
            for tbl_name in inspector.get_table_names():
                cols = inspector.get_columns(tbl_name)
                pk_cols = set(inspector.get_pk_constraint(tbl_name).get('constrained_columns', []))
                fk_list = inspector.get_foreign_keys(tbl_name)
                columns = []
                for c in cols:
                    columns.append({
                        "name": c["name"],
                        "type": str(c["type"]),
                        "nullable": c.get("nullable", True),
                        "is_pk": c["name"] in pk_cols,
                        "comment": c.get("comment", "") or "",
                    })
                schema[tbl_name] = {
                    "columns": columns,
                    "primary_key": list(pk_cols),
                    "foreign_keys": [
                        {"column": fk["constrained_columns"][0],
                         "ref_table": fk["referred_table"],
                         "ref_column": fk["referred_columns"][0] if fk["referred_columns"] else ""}
                        for fk in fk_list if fk["constrained_columns"]
                    ],
                }
            logger.info(f"[Schema] 已加载 {len(schema)} 个表")
        except Exception as e:
            logger.warning(f"[Schema] 加载失败: {e}")
            schema = {}

        self._db_schema_cache = schema
        self._db_schema_loaded_at = time.time()
        return schema

    def _build_schema_json(self) -> str:
        """将 schema 格式化为 JSON 字符串（给语义解析 Prompt）"""
        schema = self._load_db_schema()
        return json.dumps(schema, ensure_ascii=False, indent=2)

    # ========================================================================
    # 字段实际值自动加载（零硬编码）
    # ========================================================================

    _field_values_cache: Optional[Dict] = None
    _field_values_loaded_at: Optional[float] = None
    _FIELD_VALUES_TTL = 300  # 5分钟

    def _load_field_values(self) -> Dict[str, List[str]]:
        """
        对每个文本字段执行 SELECT DISTINCT ... LIMIT 100 获取实际值。
        返回 { "table.column": ["值1", "值2", ...] }
        """
        now = time.time()
        if (self._field_values_cache is not None
                and self._field_values_loaded_at
                and now - self._field_values_loaded_at < self._FIELD_VALUES_TTL):
            return self._field_values_cache

        values = {}
        schema = self._load_db_schema()
        try:
            engine = self.db.engine
            for tbl, info in schema.items():
                for col in info["columns"]:
                    t = col["type"].lower()
                    if not any(kw in t for kw in ("varchar", "char", "text", "enum")):
                        continue
                    try:
                        q = f"SELECT DISTINCT `{col['name']}` FROM `{tbl}` WHERE `{col['name']}` IS NOT NULL LIMIT 100"
                        df = pd.read_sql(q, engine)
                        vals = [str(r[0]) for r in df.itertuples(index=False) if r[0] is not None]
                        if vals:
                            key = f"{tbl}.{col['name']}"
                            values[key] = vals
                    except Exception:
                        pass
            logger.info(f"[FieldValues] 已加载 {sum(len(v) for v in values.values())} 个字段值")
        except Exception as e:
            logger.warning(f"[FieldValues] 加载失败: {e}")

        self._field_values_cache = values
        self._field_values_loaded_at = time.time()
        return values

    def _build_field_values_text(self) -> str:
        """格式化字段实际值列表为文本"""
        values = self._load_field_values()
        if not values:
            return ""
        lines = ["【字段实际值参考（用于精确匹配）】"]
        for key, vals in values.items():
            vals_str = "、".join(v for v in vals if v)
            lines.append(f"  {key}: [{vals_str}]")
        return "\n".join(lines)

    def _build_schema_text(self) -> str:
        """将 schema + 字段实际值 格式化为纯文本"""
        schema = self._load_db_schema()
        if not schema:
            return "（无法获取数据库表结构）"

        lines = []
        lines.append(f"【可用数据库表（共 {len(schema)} 个）】")
        for tbl, info in schema.items():
            col_parts = []
            for c in info["columns"]:
                col_str = f"  - {c['name']} ({c['type']})"
                if c.get('is_pk'):
                    col_str += " [主键]"
                if c.get('comment'):
                    col_str += f" {c['comment']}"
                col_parts.append(col_str)
            lines.append(f"表名: {tbl}")
            lines.extend(col_parts)
            lines.append("")

        field_vals = self._build_field_values_text()
        if field_vals:
            lines.append(field_vals)

        result = "\n".join(lines).strip()
        return result or "（无可用表）"

    # ========================================================================
    # 语义解析（qwen-turbo）— 通用，无硬编码词汇
    # ========================================================================

    def _semantic_parse(self, question: str) -> Dict:
        """
        用辅助模型解析用户问题为结构化意图 JSON。

        返回:
            {
                "intent": str,
                "primary_table": str,
                "filters": [...],
                "time_range": {...},
                "aggregation": {...},
                "group_by": [...],
                "clarification_needed": bool,
                "missing_info": [...],
            }
            失败时返回 {"clarification_needed": False} 让主模型接管
        """
        try:
            from utils.model_router import get_router
            router = get_router()
            schema_json = self._build_schema_json()
            field_values = self._build_field_values_text()
            term_hints = self._build_term_hints()
            prompt = SEMANTIC_PARSE_PROMPT.format(
                schema_json=schema_json[:5000],
                field_values=field_values or "（无字段值参考）",
                term_hints=term_hints or "（无术语映射）",
                user_question=question,
            )
            client = router._get_client("intent")
            raw = client.generate(prompt)
            if not raw:
                return {"clarification_needed": False}

            # 解析 JSON
            json_match = re.search(r'\{.*\}', raw, re.DOTALL)
            if json_match:
                parsed = json.loads(json_match.group())
                logger.info(f"[语义解析] intent={parsed.get('intent')}, table={parsed.get('primary_table')}, "
                           f"needs_clarify={parsed.get('clarification_needed')}")
                return parsed
        except Exception as e:
            logger.debug(f"[语义解析] 失败 (非阻塞): {e}")
        return {"clarification_needed": False}

    # ========================================================================
    # Schema 检索（保留原有混合检索作为补充）
    # ========================================================================

    def _retrieve_schema(self, question: str) -> Tuple[str, str]:
        """
        检索与问题最相关的 Schema 信息。
        优先使用 _load_db_schema()，混合检索作为补充。
        """
        # 混合检索
        try:
            retrieved = self.hybrid_retriever.retrieve_formatted(question)
        except Exception as e:
            logger.warning(f"[Agent] 混合检索失败，使用完整 Schema: {e}")
            retrieved = ""

        # 获取完整 DDL（作为补充）
        full_ddl = ""
        try:
            ddl_dict = self.db.get_table_ddl()
            full_ddl = "\n\n".join(
                f"-- {name} --\n{sql}" for name, sql in ddl_dict.items()
            )
        except Exception as e:
            logger.warning(f"[Agent] DDL 提取失败: {e}")

        # 补充：获取所有表的列名+类型摘要（确保 LLM 知道所有可用字段）
        table_summary = ""
        try:
            tables = self.db.get_table_names()
            if tables:
                from sqlalchemy import inspect as sa_inspect
                inspector = sa_inspect(self.db.engine)
                parts = []
                for tbl in tables:
                    cols = inspector.get_columns(tbl)
                    col_strs = [f"{c['name']} ({str(c['type'])})" for c in cols]
                    parts.append(f"    {tbl}: {', '.join(col_strs)}")
                if parts:
                    table_summary = "\n【数据库所有表及字段】\n" + "\n".join(parts)
        except Exception as e:
            logger.warning(f"[Agent] 表摘要提取失败: {e}")

        # 如果混合检索没有结果，使用完整表摘要
        if not retrieved and table_summary:
            retrieved = table_summary
        elif retrieved:
            retrieved += "\n" + table_summary

        return retrieved, full_ddl

    # ========================================================================
    # SQL 提取
    # ========================================================================

    @staticmethod
    def _extract_sql(text: str) -> str:
        """
        从 LLM 输出中提取 SQL 语句。

        处理多种输出格式:
        - 标准格式: ```sql ... ```
        - 旧格式: 【SQL】... SQL ...
        - "SQL:" 前缀
        - 纯 SQL 语句

        如果检测到澄清 JSON 格式，返回原始 JSON 字符串。
        """
        text = text.strip()

        # 检测是否为 JSON 澄清格式（支持 ```json 包裹和纯 JSON）
        json_pattern = re.search(r'```(?:json)?\s*\n?(\{"clarification_needed".*?\n?```)', text, re.DOTALL)
        if json_pattern:
            return json_pattern.group(1).strip()
        if text.startswith('{"clarification_needed"'):
            return text

        # 优先提取 ```sql 代码块（明确标记的语言）
        sql_block = re.search(r'```sql\s*\n?(.*?)\n?```', text, re.DOTALL | re.IGNORECASE)
        if not sql_block:
            # 回退：提取最后一个 ``` 代码块（更可能是 SQL）
            all_blocks = list(re.finditer(r'```\s*\n?(.*?)\n?```', text, re.DOTALL))
            if all_blocks:
                sql_block = all_blocks[-1]  # 取最后一个
        if sql_block:
            extracted = sql_block.group(1).strip()
            # 确保提取的内容以 SELECT/WITH 开头
            match = re.search(r'(SELECT|WITH)\s', extracted, re.IGNORECASE)
            if match:
                extracted = extracted[match.start():]
            # 检查 SQL 是否被截断（缺少结尾子句）
            if re.search(r'\b(GROUP|ORDER|WHERE|HAVING)\s*$', extracted, re.IGNORECASE):
                logger.warning(f"[提取] SQL 可能被截断，末尾不完整: ...{extracted[-30:]}")
            return extracted

        # 尝试提取 【SQL】 标记后的内容（旧 CoT 格式）
        cot_match = re.search(r'【SQL】\s*\n?(.*?)$', text, re.DOTALL)
        if cot_match:
            candidate = cot_match.group(1).strip()
            sql_block = re.search(r'```(?:sql)?\s*\n?(.*?)\n?```', candidate, re.DOTALL)
            if sql_block:
                return sql_block.group(1).strip()
            if re.match(r'^(SELECT|WITH)\s', candidate, re.IGNORECASE):
                return candidate

        # 尝试提取 "SQL:" 后面的内容
        sql_prefix = re.search(r'SQL:\s*(.*)', text, re.DOTALL)
        if sql_prefix:
            return sql_prefix.group(1).strip()

        # 从整体输出中提取 SELECT/WITH 开头的行
        lines = text.strip().split("\n")
        sql_lines = []
        for line in lines:
            line = line.strip()
            if line.upper().startswith(("SELECT", "WITH", "--")):
                sql_lines.append(line)
            elif sql_lines and line and not line.startswith(("【", "```", "{")):
                sql_lines.append(line)

        if sql_lines:
            result = "\n".join(sql_lines)
            result = re.sub(r'^-- .*\n?', '', result).strip()
            return result if result else text

        return text

    # ========================================================================
    # SQL 生成（新流程：语义解析 → 反问 → SQL 生成）
    # ========================================================================

    def generate_sql(self, question: str) -> str:
        """
        SQLBot 流程:
        0. 用户查询预处理（术语替换）
        1. 加载 Schema + 字段实际值
        2. qwen-turbo 语义解析 → 结构化 JSON
        3. 如需反问 → 返回 missing_info
        4. deepseek 根据解析结果 + Schema 生成 SQL
        5. 辅助模型验证
        6. 安全校验

        返回: (sql, token_estimate)
        """
        # ---- Step 0: 查询预处理（零硬编码术语替换）----
        self._load_distinct_values()
        normalized_q = self.normalize_user_query(question)
        logger.info(f"[Agent] 原始问题: '{question}' → 标准化: '{normalized_q}'")

        # 检查缓存
        cache_key = f"sql:{normalized_q}"
        if cache_key in self._result_cache:
            cached = self._result_cache[cache_key]
            if time.time() - cached["ts"] < self._result_cache_ttl:
                logger.info(f"[Agent] 缓存命中: {normalized_q}")
                return cached["sql"], cached["tokens"]

        schema_text = self._build_schema_text()
        schema_json = self._build_schema_json()

        # ---- Step 1: 语义解析 ----
        parsed = self._semantic_parse(normalized_q)

        # ---- Step 2: 反问机制 ----
        if parsed.get("clarification_needed"):
            missing = parsed.get("missing_info", [])
            q = parsed.get("clarification_question", "")
            if not q and missing:
                q = f"请补充以下信息: {', '.join(missing)}"
            if not q:
                q = "请更详细地描述您要查询的内容"
            logger.info(f"[Agent] 需要澄清: {q}")
            return json.dumps({"clarification_needed": True, "question": q}), 0

        # ---- Step 3: 构建 SQL 生成 Prompt ----
        db_type = self.db.active_db_type
        limit = parsed.get("limit", 50)
        term_hints = self._build_term_hints()

        # 注入匹配的 skill 指令
        try:
            from skill_registry import SkillRegistry
            registry = SkillRegistry()
            skill_instructions = registry.get_instructions(normalized_q, list(self._load_db_schema().keys()))
            if skill_instructions:
                logger.info(f"[Agent] 注入 {skill_instructions.count('技能')} 个 skill 指令")
        except Exception:
            skill_instructions = ""

        prompt = SQL_FROM_INTENT_PROMPT.format(
            db_type=db_type,
            schema_str=schema_text + ("\n\n" + skill_instructions if skill_instructions else ""),
            term_hints=term_hints or "（无）",
            parsed_intent_json=json.dumps(parsed, ensure_ascii=False, indent=2),
            user_question=normalized_q,
            limit=limit,
        )

        token_estimate = self.llm.estimate_tokens(prompt)

        # ---- Step 4: 主模型生成 SQL（多模型共识）----
        try:
            raw_output = self.llm.generate(prompt)
        except Exception as e:
            logger.error(f"[Agent] 主模型失败: {e}")
            return f"ERROR: 主模型生成失败: {e}", 0

        sql = self._extract_sql(raw_output)

        # 共识检查：用辅助模型验证 WHERE 值是否匹配字段实际值
        if sql and not sql.startswith(('{"', 'ERROR:')) and "WHERE" in sql.upper():
            try:
                from utils.model_router import get_router
                router = get_router()
                validation = router.route("sql_validate",
                    question=normalized_q, sql=sql,
                    schema_str=schema_text, db_type=db_type)
                if not validation.get("is_valid", True):
                    issues = validation.get("issues", [])
                    if issues:
                        logger.warning(f"[共识] 验证发现 {len(issues)} 个问题: {issues}")
                        # 重试一次，强调字段值匹配
                        try:
                            raw2 = self.llm.generate(prompt + "\n\n【重要】仔细检查 WHERE 条件中的文本值是否与【字段实际值参考】完全一致！用户口语已被预处理，请使用预处理后的值。")
                            sql2 = self._extract_sql(raw2)
                            if sql2 and sql2 != sql and "WHERE" in sql2.upper():
                                fv = self._load_field_values()
                                score1 = sum(1 for v in re.findall(r"'([^']+)'", sql) if any(v in vals for vals in fv.values()))
                                score2 = sum(1 for v in re.findall(r"'([^']+)'", sql2) if any(v in vals for vals in fv.values()))
                                if score2 > score1:
                                    sql = sql2
                                    logger.info(f"[共识] 选用 SQL2 (匹配 {score2} 个字段值)")
                        except Exception:
                            pass
            except Exception as e:
                logger.debug(f"[共识] 验证失败 (非阻塞): {e}")

        # ---- Step 5: 辅助模型验证 ----
        if sql and not sql.startswith(('{"', 'ERROR:')):
            try:
                from utils.model_router import get_router
                router = get_router()
                validation = router.route("sql_validate",
                    question=question, sql=sql,
                    schema_str=schema_text, db_type=db_type)
                if not validation.get("is_valid", True):
                    logger.warning(f"[Agent] SQL 验证: {validation.get('issues', [])}")
            except Exception as e:
                logger.debug(f"[Agent] SQL 验证失败 (非阻塞): {e}")

        # 后处理
        if not sql.startswith('{"'):
            sql_fixed = fix_sql_quoting(sql)
            if sql_fixed != sql:
                sql = sql_fixed

        # 写入结果缓存
        if sql and not sql.startswith(('{"', 'ERROR:')):
            cache_key = f"sql:{normalized_q}"
            self._result_cache[cache_key] = {"sql": sql, "tokens": token_estimate, "ts": time.time()}

        logger.info(f"[Agent] SQL:\n{sql}")
        return sql, token_estimate

    # ========================================================================
    # 自我修正
    # ========================================================================

    def _correct_sql(
        self, question: str, wrong_sql: str, error_message: str,
        schema_text: str, attempt: int,
    ) -> Optional[str]:
        """
        根据错误信息修正 SQL（使用 Error Recovery Chain）。

        参数:
            question: 用户问题
            wrong_sql: 之前生成但执行失败的 SQL
            error_message: 数据库返回的错误信息
            schema_text: 表结构信息
            attempt: 当前是第几次修正

        返回:
            修正后的 SQL，如果仍失败返回 None
        """
        logger.info(f"[Agent] 第 {attempt} 次自我修正...")

        # 使用 Error Recovery Chain 生成针对性修正策略
        recovery_strategy = ""
        if self._error_recovery is not None:
            # 尝试用 sqlglot 验证错误分类
            error_type = "未知错误"
            if self._enhanced_validator is not None:
                _, v_errors = self._enhanced_validator.validate_all(wrong_sql)
                if v_errors:
                    error_type = v_errors[0].error_type
            recovery_strategy = self._error_recovery.get_recovery_strategy(
                error_type, error_message
            )

        prompt = SQL_CORRECTION_PROMPT_TEMPLATE.format(
            db_type=self.db.active_db_type,
            schema_str=schema_text,
            user_question=question,
            wrong_sql=wrong_sql,
            error_message=error_message,
            recovery_strategy=recovery_strategy,
        )

        raw_output = self.llm.generate(prompt)
        sql = self._extract_sql(raw_output)

        # 后处理：修正表名/列名单引号错误
        sql = fix_sql_quoting(sql)   # 确保修正后的 SQL 没有单引号表名
        return sql

        # sqlglot 校验修正后的 SQL
        if self._enhanced_validator is not None:
            is_valid, v_errors = self._enhanced_validator.validate_all(sql)
            if v_errors:
                for verr in v_errors:
                    logger.warning(f"[Agent] 修正后 sqlglot 校验警告: [{verr.error_type}] {verr.message}")

        # 再次校验
        valid, err = self.validator.validate(sql)
        if not valid:
            logger.warning(f"[Agent] 修正后的 SQL 仍未通过校验: {err}")
            return None

        return sql

    # ========================================================================
    # 结果反思 — 0 行时自动修正值并重试
    # ========================================================================

    def _reflect_on_result(self, question: str, sql: str, result: List,
                           field_values: Dict[str, List[str]]) -> Optional[str]:
        """
        执行后反思：如果 0 行或执行报错，用 qwen-turbo 分析原因并修正 SQL。
        返回修正后的 SQL，或 None 表示无需修正。
        """
        if result and len(result) > 0:
            return None

        logger.info("[反思] 结果为空，用辅助模型分析原因...")

        # 用 qwen-turbo 分析错误并给出修正建议
        try:
            from utils.model_router import get_router
            router = get_router()
            fv_text = self._build_field_values_text()
            term_hints = self._build_term_hints()
            prompt = f"""SQL 查询返回 0 行，请分析原因并给出修正后的 SQL。

【用户问题】
{question}

【失败的 SQL】
{sql}

【字段实际值参考】
{fv_text}

【术语同义词映射】
{term_hints}

可能的原因：
1. WHERE 条件中的值不匹配数据库实际存储的值（最常见）
2. 表名或字段名错误
3. 过滤条件过于严格

请输出修正后的 SQL，用 ```sql``` 包裹。只输出 SQL，不要解释。"""
            client = router._get_client("sql_validate")
            raw = client.generate(prompt)
            if raw:
                corrected = TextToSQLAgent._extract_sql(raw)
                if corrected and corrected != sql:
                    logger.info(f"[反思] qwen-turbo 修正 SQL")
                    return corrected
        except Exception as e:
            logger.debug(f"[反思] 辅助模型调用失败: {e}")

        # 降级：从 field_values 中找近似匹配进行简单替换
        if field_values and "WHERE" in sql.upper():
            for val_match in re.findall(r"'([^']+)'", sql):
                for key, valid_vals in field_values.items():
                    for valid in valid_vals:
                        if val_match != valid and (valid in val_match or val_match in valid):
                            new_sql = sql.replace(f"'{val_match}'", f"'{valid}'")
                            logger.info(f"[反思] 模糊替换 '{val_match}' → '{valid}'")
                            return new_sql
                        # 检查术语映射
                        for field, mappings in self.term_map.get("value_mappings", {}).items():
                            if val_match in mappings and mappings[val_match] == valid:
                                new_sql = sql.replace(f"'{val_match}'", f"'{valid}'")
                                logger.info(f"[反思] 术语替换 '{val_match}' → '{valid}'")
                                return new_sql

        logger.info("[反思] 未找到可修正方案")
        return None

    # ========================================================================
    # 主执行链路
    # ========================================================================

    def run(self, question: str, use_cache: bool = True) -> Dict[str, Any]:
        """
        执行完整的 Text-to-SQL 链路。

        参数:
            question: 用户的自然语言问题
            use_cache: 是否启用缓存

        返回:
            {
                "question": str,
                "sql": str,
                "result": List[Dict] | None,
                "columns": List[str] | None,
                "error": str | None,
                "cache_hit": bool,
                "cache_source": str | None,
                "retries": int,
                "token_estimate": int,
                "execution_time": float,
                "trace": Dict | None,
            }
        """
        start_time = time.time()
        result: Dict[str, Any] = {
            "question": question,
            "sql": None,
            "result": None,
            "columns": None,
            "error": None,
            "cache_hit": False,
            "cache_source": None,
            "retries": 0,
            "token_estimate": 0,
            "execution_time": 0,
            "trace": None,
        }

        # 启动链路追踪
        recorder = self.trace_recorder
        if recorder:
            recorder.start_run(question)

        # ---- Step 1: 查缓存 ----
        if use_cache:
            try:
                from cache import get_cache
                cache = get_cache()
                t0 = time.time()
                cached = cache.get(question)
                t1 = time.time()
                if cached:
                    if recorder:
                        recorder.record("cache_check", "hit", {
                            "source": cached["source"],
                            "similarity": cached.get("similarity"),
                        }, t1 - t0)

                    result["sql"] = cached["sql"]
                    result["result"] = cached["result"]
                    result["cache_hit"] = True
                    result["cache_source"] = cached["source"]
                    result["execution_time"] = time.time() - start_time

                    # 如果缓存有结果，将结果转为 DataFrame 获取 columns
                    if isinstance(cached["result"], list) and cached["result"]:
                        result["columns"] = list(cached["result"][0].keys())

                    logger.info(f"[Agent] 缓存命中 ({cached['source']})，跳过 LLM")
                    if recorder:
                        recorder.end_run()
                        result["trace"] = recorder.end_run()
                    return result
                else:
                    if recorder:
                        recorder.record("cache_check", "miss", {}, t1 - t0)
            except Exception as e:
                logger.warning(f"[Agent] 缓存查询失败，继续流程: {e}")
                if recorder:
                    recorder.record("cache_check", "error", {"error": str(e)}, 0)

        # ---- Step 2: 生成 SQL ----
        t0 = time.time()
        schema_text, full_ddl = self._retrieve_schema(question)
        t1 = time.time()
        if recorder:
            recorder.record("schema_retrieval", "success", {
                "has_schema": bool(schema_text),
            }, t1 - t0)

        schema_for_prompt = schema_text or full_ddl or "（无法获取表结构）"

        try:
            t0 = time.time()
            sql, token_estimate = self.generate_sql(question)
            t1 = time.time()
            if recorder:
                recorder.record("llm_generate", "success", {
                    "token_estimate": token_estimate,
                    "sql_length": len(sql),
                }, t1 - t0)
        except Exception as llm_err:
            # LLM 不可用时，使用基于关键词的 SQL 生成器作为降级方案
            logger.warning(f"[Agent] LLM 调用失败，使用关键词降级方案: {llm_err}")
            t0 = time.time()
            sql = self._fallback_generate_sql(question)
            t1 = time.time()
            token_estimate = len(question) * 3
            if recorder:
                recorder.record("llm_generate", "fallback", {
                    "error": str(llm_err)[:200],
                }, t1 - t0)
        result["sql"] = sql
        result["token_estimate"] = token_estimate

        # ---- 检测是否为澄清请求 ----
        if isinstance(sql, str) and sql.startswith('{"clarification_needed"'):
            try:
                clarification = json.loads(sql)
                result["error"] = None
                result["clarification"] = clarification.get("question", "")
                result["execution_time"] = time.time() - start_time
                logger.info(f"[Agent] 需要澄清: {result['clarification']}")
                if recorder:
                    recorder.end_run()
                    result["trace"] = recorder.end_run()
                return result
            except json.JSONDecodeError:
                pass

        # ---- Step 3: 安全校验 ----
        sql = self._extract_sql(sql)
        valid, err_msg = self.validator.validate(sql)
        if not valid:
            result["error"] = f"SQL 安全校验失败: {err_msg}"
            result["execution_time"] = time.time() - start_time
            if recorder:
                recorder.record("validation", "failure", {
                    "error": err_msg,
                }, time.time() - t1)
                recorder.end_run()
                result["trace"] = recorder.end_run()
            return result
        if recorder:
            recorder.record("validation", "success", {}, time.time() - t1)

        # ---- Step 4: 执行 SQL（只读事务）与自我修正循环 ----
        max_retries = self.config.max_retries
        for attempt in range(max_retries + 1):  # 首次执行 + max_retries 次修正
            try:
                t0 = time.time()
                logger.info(f"[Agent] 执行 SQL (尝试 {attempt + 1}/{max_retries + 1})")
                # 在只读事务中执行
                # 最终执行前再次强制修复单引号标识符
                sql_final = fix_sql_quoting(sql)
                df = self.db.query_readonly(sql_final)
                # 同步更新 result 中的 sql 变量
                sql = sql_final
                t1 = time.time()
                result["result"] = df.to_dict(orient="records")
                result["columns"] = df.columns.tolist()
                result["retries"] = attempt
                if recorder:
                    recorder.record("sql_execution", "success", {
                        "rows": len(df),
                        "attempt": attempt + 1,
                    }, t1 - t0)
                break  # 执行成功，跳出循环
            except Exception as e:
                error_msg = str(e)
                t1 = time.time()
                logger.warning(f"[Agent] SQL 执行失败 (尝试 {attempt + 1}): {error_msg}")

                if recorder:
                    recorder.record("sql_execution", "failure", {
                        "error": error_msg[:200],
                        "attempt": attempt + 1,
                    }, t1 - t0)

                if attempt < max_retries:
                    # 尝试修正
                    t0 = time.time()
                    corrected_sql = self._correct_sql(
                        question, sql, error_msg, schema_for_prompt, attempt + 1
                    )
                    t1 = time.time()
                    if corrected_sql:
                        sql = corrected_sql
                        result["sql"] = sql
                        if recorder:
                            recorder.record("sql_correction", "success", {
                                "attempt": attempt + 1,
                            }, t1 - t0)
                        # 校验修正后的 SQL
                        valid, err = self.validator.validate(sql)
                        if not valid:
                            result["error"] = f"SQL 语法有误，请修改后重试"
                            break
                    else:
                        result["error"] = f"无法自动修正此查询，请换个问法"
                        if recorder:
                            recorder.record("sql_correction", "failure", {
                                "attempt": attempt + 1,
                            }, t1 - t0)
                        break
                else:
                    result["error"] = f"查询执行失败，请检查数据或换个问法"

        # ---- Step 4.5: 结果反思（0 行时自动修正值） ----
        if result["error"] is None and result["result"] is not None:
            try:
                # 只对 0 行结果进行反思
                field_values = self._load_field_values()
                reflected_sql = self._reflect_on_result(question, sql, result["result"], field_values)
                if reflected_sql and reflected_sql != sql:
                    logger.info(f"[反思] 重新执行修正后的 SQL")
                    valid, err = self.validator.validate(reflected_sql)
                    if valid:
                        df2 = self.db.query_readonly(reflected_sql)
                        result2 = df2.to_dict(orient="records")
                        if result2:
                            result["result"] = result2
                            result["columns"] = df2.columns.tolist()
                            result["sql"] = reflected_sql
                            result["reflected"] = True
                            logger.info(f"[反思] 修正后查询到 {len(result2)} 行数据")
            except Exception as e:
                logger.debug(f"[反思] 执行失败 (非阻塞): {e}")

        # ---- Step 5: SQL 优化分析 ----
        if result["error"] is None and result.get("sql") and hasattr(self, 'sql_optimizer') and self.sql_optimizer:
            try:
                opt = self.sql_optimizer.analyze(result["sql"])
                if opt["suggestions"] or opt["indexes"]:
                    result["optimization"] = opt
            except Exception:
                pass

        # ---- Step 6: 自动图表生成 ----
        if result["error"] is None and result["result"]:
            try:
                chart_config = self.generate_chart_config(question, pd.DataFrame(result["result"]))
                if chart_config and chart_config.get("needs_chart"):
                    result["chart_config"] = chart_config
            except Exception as e:
                logger.debug(f"[Agent] 图表自动生成跳过: {e}")

        # ---- Step 7: 辅助模型生成结果解释 ----
        if result["error"] is None and result["result"]:
            try:
                from utils.model_router import get_router
                router = get_router()
                explanation = router.route("explain",
                    question=question, sql=result.get("sql", ""),
                    result_rows=result.get("result", []))
                result["explanation"] = explanation
            except Exception as e:
                logger.debug(f"[Agent] 结果解释生成失败 (非阻塞): {e}")

        # ---- Step 6: 写入缓存（如果成功） ----
        if result["error"] is None and use_cache and result["result"] is not None:
            try:
                from cache import get_cache
                cache = get_cache()
                cache.set(question, result["sql"], result["result"], token_estimate)
            except Exception as e:
                logger.warning(f"[Agent] 缓存写入失败: {e}")

        result["execution_time"] = time.time() - start_time

        # 完成追踪
        if recorder:
            result["trace"] = recorder.end_run()

        return result

    # ========================================================================
    # generate_and_execute — 统一入口
    # ========================================================================

    def generate_and_execute(self, question: str) -> Dict[str, Any]:
        """
        统一的 Text-to-SQL 入口：生成 SQL → 执行 → 返回结果。

        流程:
        1. 生成 SQL（使用增强 Prompt：同义词映射 + Schema + Few-Shot）
        2. 在只读事务中执行 SQL
        3. 如果失败，用错误信息修正 SQL（最多重试 2 次）
        4. 仍失败则返回友好提示

        参数:
            question: 用户的自然语言问题

        返回:
            {
                "question": str,            # 原始问题
                "sql": str | None,           # 生成的 SQL
                "result": List[Dict] | None, # 查询结果
                "columns": List[str] | None, # 列名
                "error": str | None,         # 错误信息
                "clarification": str | None, # 需要澄清时不为空
                "cache_hit": bool,           # 是否缓存命中
                "retries": int,              # 重试次数
                "execution_time": float,     # 总耗时（秒）
            }
        """
        return self.run(question=question, use_cache=True)

    # ========================================================================
    # 关键词降级 SQL 生成器（LLM 不可用时使用）
    # ========================================================================

    def _fallback_generate_sql(self, question: str) -> str:
        """
        基于关键词匹配的 SQL 生成器。

        当 LLM 不可用时，通过关键词匹配生成预定义的 SQL 查询。
        先检测可用表，动态生成默认查询；再尝试硬编码模板（兼容旧Schema）。
        """
        q = question.lower()

        # 检测可用表
        available_tables = self.db.get_table_names()
        first_table = available_tables[0] if available_tables else None

        # 预定义的 SQL 模板（兼容旧电商 Schema）
        templates = [
            # ---- 用户统计 ----
            (["用户", "总数", "多少"], "SELECT COUNT(*) AS total_users FROM users" if "users" in available_tables else None),
            (["用户", "省份", "每个", "分布", "各省"], "SELECT province, COUNT(*) AS user_count FROM users GROUP BY province ORDER BY user_count DESC" if "users" in available_tables else None),
            (["用户", "活跃"], "SELECT COUNT(*) AS active_users FROM users WHERE is_active = 1" if "users" in available_tables else None),
            (["会员", "等级", "vip", "各级"], "SELECT vip_level, COUNT(*) AS user_count FROM users GROUP BY vip_level ORDER BY user_count DESC" if "users" in available_tables else None),

            # ---- 订单统计 ----
            (["省份", "销售", "总金额", "各省", "每个"], "SELECT u.province, SUM(o.total_amount) AS total_sales FROM orders o JOIN users u ON o.user_id = u.user_id WHERE o.status = '已完成' GROUP BY u.province ORDER BY total_sales DESC" if all(t in available_tables for t in ["orders", "users"]) else None),
            (["每月", "月", "销售", "趋势", "每个月"], "SELECT strftime('%Y-%m', order_date) AS month, SUM(total_amount) AS monthly_sales FROM orders WHERE status = '已完成' GROUP BY month ORDER BY month" if "orders" in available_tables else None),
            (["每日", "天", "销售", "趋势", "每天", "日"], "SELECT order_date, SUM(total_amount) AS daily_sales FROM orders WHERE status = '已完成' GROUP BY order_date ORDER BY order_date" if "orders" in available_tables else None),
            (["消费", "最多", "前", "top", "排名"], "SELECT u.username, u.city, SUM(o.total_amount) AS total_spent FROM orders o JOIN users u ON o.user_id = u.user_id WHERE o.status = '已完成' GROUP BY o.user_id ORDER BY total_spent DESC LIMIT 10" if all(t in available_tables for t in ["orders", "users"]) else None),
            (["订单", "状态", "完成", "各状态"], "SELECT status, COUNT(*) AS order_count FROM orders GROUP BY status ORDER BY order_count DESC" if "orders" in available_tables else None),
            (["订单", "总数", "多少"], "SELECT COUNT(*) AS total_orders FROM orders" if "orders" in available_tables else None),
            (["金额", "最高", "大额"], "SELECT o.order_id, u.username, o.total_amount FROM orders o JOIN users u ON o.user_id = u.user_id ORDER BY o.total_amount DESC LIMIT 10" if all(t in available_tables for t in ["orders", "users"]) else None),

            # ---- 商品统计 ----
            (["类别", "商品", "分类", "品类", "种类"], "SELECT p.category, COUNT(*) AS product_count, AVG(p.price) AS avg_price FROM products p GROUP BY p.category" if "products" in available_tables else None),
            (["商品", "平均", "价格"], "SELECT AVG(price) AS avg_price FROM products" if "products" in available_tables else None),
            (["最贵", "价格", "最高", "高价"], "SELECT product_name, category, price FROM products ORDER BY price DESC LIMIT 10" if "products" in available_tables else None),
            (["库存", "最多", "充足"], "SELECT product_name, category, stock FROM products ORDER BY stock DESC LIMIT 10" if "products" in available_tables else None),
            (["毛利率", "利润", "margin"], "SELECT product_name, price, cost, ROUND((price - cost) / price * 100, 2) AS margin_rate FROM products ORDER BY margin_rate DESC" if "products" in available_tables else None),
        ]

        for keywords, sql in templates:
            if sql is not None and all(kw in q for kw in keywords):
                return sql

        # 宽松匹配（仅当存在对应表时）
        if "users" in available_tables and any(kw in q for kw in ["用户", "客户", "会员"]):
            return "SELECT * FROM users LIMIT 20"
        if "orders" in available_tables and any(kw in q for kw in ["订单", "下单", "购买", "买了"]):
            return "SELECT o.*, u.username FROM orders o JOIN users u ON o.user_id = u.user_id ORDER BY o.order_date DESC LIMIT 20"
        if "products" in available_tables and any(kw in q for kw in ["商品", "产品", "东西", "物品"]):
            return "SELECT * FROM products ORDER BY price DESC LIMIT 20"
        if "orders" in available_tables and any(kw in q for kw in ["销售", "卖了", "收入", "金额", "钱"]):
            return "SELECT strftime('%Y-%m', order_date) AS month, SUM(total_amount) AS monthly_sales FROM orders WHERE status = '已完成' GROUP BY month ORDER BY month"

        # 默认：枚举可用表
        if first_table:
            # 尝试获取表的列信息
            try:
                from sqlalchemy import inspect as sa_inspect
                inspector = sa_inspect(self.db.engine)
                cols = inspector.get_columns(first_table)
                col_names = [c["name"] for c in cols[:5]]  # 最多取5列
                cols_select = ", ".join(f'"{c}"' for c in col_names)
                return f'SELECT {cols_select} FROM "{first_table}" LIMIT 20'
            except Exception:
                return f'SELECT * FROM "{first_table}" LIMIT 20'
        return "SELECT 1 AS info"

    def generate_chart_config(self, question: str, df: 'pd.DataFrame') -> Optional[Dict]:
        """根据查询结果自动生成 ECharts 配置"""
        if df.empty or len(df.columns) < 2:
            return {"needs_chart": False}
        preview = df.head(50).to_string(max_colwidth=30)
        prompt = f"""根据用户问题 {question} 和数据预览：
{preview}
输出 JSON：{{"needs_chart":true/false,"chart_type":"bar|line|pie","config":{{"xAxis":{{"data":[...]}},"series":[{{"data":[...]}}],"title":{{"text":"..."}}}}}}
只输出 JSON。"""
        try:
            from agent import LLMClient
            client = LLMClient()
            raw = client.generate(prompt)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                if parsed.get("needs_chart"):
                    return parsed
            return {"needs_chart": False}
        except Exception:
            return {"needs_chart": False}

    def get_schema_summary(self) -> str:
        """获取数据库结构摘要（给 LLM 查看用）"""
        try:
            info_list = self.db.get_table_info()
            parts = []
            for info in info_list:
                cols = ", ".join(
                    f"{c['name']}({c['type']})" for c in info["columns"]
                )
                parts.append(f"{info['table_name']}({cols})")
            return " | ".join(parts)
        except Exception as e:
            return f"获取schema失败: {e}"


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
    print("Text-to-SQL Agent 测试")
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
