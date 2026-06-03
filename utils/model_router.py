"""
==============================================================================
模型路由 — 动态分配主模型/辅助模型任务
==============================================================================
设计思路：
  主模型（DeepSeek-V4-Pro）负责核心 SQL 生成，成本较高。
  辅助模型（如 qwen-turbo / GLM-4-Flash，免费）负责辅助任务：
    1. 意图识别与消歧 — 判断问题是否清晰，是否需要反问
    2. SQL 语法/逻辑验证 — 检查生成的 SQL 是否有明显错误
    3. 结果自然语言解释 — 将数据表格转为业务可读的文字

  当主模型调用失败时，可降级使用辅助模型生成 SQL。
==============================================================================
"""

import json
import logging
from typing import Optional, Dict, Any, List, Tuple

from config import CONFIG

logger = logging.getLogger("model_router")

# ============================================================================
# 辅助任务 Prompt 模板
# ============================================================================

INTENT_CLARIFICATION_PROMPT = """
你是数据库查询意图分析助手。请判断用户的问题是否足够清晰，能否直接生成 SQL 查询。

【用户问题】
{user_question}

【数据库表结构】
{schema_str}

请返回以下 JSON 格式（只输出 JSON，不要输出其他内容）：
{{
    "is_clear": true/false,
    "clarification_needed": true/false,
    "clarification_question": "如果不清晰，这里填写需要反问用户的问题；如果清晰，填空字符串",
    "missing_info": ["缺失的信息列表，如'时间范围'、'具体表名'等"]
}}

判断标准：
- 如果问题中包含模糊词汇（"最近"、"业绩大涨"、"高价值客户"等），视为不清晰
- 如果问题未指定表名或关键过滤条件，视为不清晰
- 如果问题中的业务术语无法映射到数据库表/字段，视为不清晰
"""

SQL_VALIDATION_PROMPT = """
你是 SQL 语法检查专家。请检查以下 SQL 是否存在语法或逻辑错误。

【数据库类型】
{db_type}

【数据库表结构（含字段类型）】
{schema_str}

【用户问题】
{user_question}

【生成的 SQL】
```sql
{sql}
```

请分析并返回 JSON（只输出 JSON）：
{{
    "is_valid": true/false,
    "issues": ["问题列表，如'字段名xx不存在'、'缺少GROUP BY'等"],
    "suggestion": "修改建议，如果没有问题填空字符串"
}}

重点检查：
- 表名和字段名在 schema 中是否存在
- 聚合函数（SUM/COUNT/AVG）是否缺少 GROUP BY
- WHERE 条件中的字段类型是否匹配（数值 vs 字符串）
- JOIN 条件是否完整
"""

EXPLANATION_PROMPT = """
你是数据分析报告撰写专家。请根据 SQL 查询结果，用简洁易懂的语言向业务人员解释数据结论。

【用户问题】
{user_question}

【SQL 语句】
```sql
{sql}
```

【查询结果】
{result_rows}

请用中文、自然语言总结数据结论。要求：
- 突出关键数字和趋势
- 用业务语言而非技术语言
- 如果结果为空，分析可能原因并给出建议
- 控制在 100 字以内
"""

# ============================================================================
# 辅助模型客户端
# ============================================================================

class AuxLLMClient:
    """辅助模型客户端 — 每个任务可指定不同的免费模型"""

    def __init__(self, task_type: str = "default"):
        """
        参数:
            task_type: 任务类型 ("intent" / "sql_validate" / "explain" / "default")
        """
        self.cfg = CONFIG.aux_llm
        self.task_type = task_type
        spec = self.cfg.get_spec(task_type)
        self.model_name = spec.model
        self.api_key = spec.api_key
        self.base_url = spec.base_url
        self._client = None
        self._init_client()

    def _init_client(self):
        """初始化辅助模型客户端"""
        try:
            from langchain_openai import ChatOpenAI
            kwargs = dict(
                model=self.model_name,
                temperature=0.1,
                max_tokens=300,
                api_key=self.api_key,
            )
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = ChatOpenAI(**kwargs)
            logger.info(f"[AuxLLM][{self.task_type}] 模型: {self.model_name}")
        except ImportError:
            logger.error("[AuxLLM] langchain-openai 未安装")
            raise

    def generate(self, prompt: str, timeout: int = 15) -> str:
        """调用当前任务的辅助模型生成文本"""
        if not self._client:
            logger.warning(f"[AuxLLM][{self.task_type}] 客户端未初始化")
            return ""
        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                result = loop.run_in_executor(None, self._client.invoke, prompt).result(timeout)
            except RuntimeError:
                result = self._client.invoke(prompt)
            text = result.content if hasattr(result, 'content') else str(result)
            return text.strip()
        except Exception as e:
            logger.warning(f"[AuxLLM][{self.task_type}] 调用失败: {e}")
            return ""

    def generate_json(self, prompt: str, timeout: int = 15) -> Optional[Dict]:
        """调用辅助模型并解析 JSON 返回。失败时返回 None。"""
        text = self.generate(prompt, timeout=timeout)
        if not text:
            return None
        import re
        json_match = re.search(r'```(?:json)?\s*\n?(\{.*?\})\s*\n?```', text, re.DOTALL)
        if json_match:
            text = json_match.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find('{')
            end = text.rfind('}')
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end+1])
                except json.JSONDecodeError:
                    pass
            logger.warning(f"[AuxLLM][{self.task_type}] JSON 解析失败: {text[:200]}")
            return None


# ============================================================================
# 路由函数
# ============================================================================

class ModelRouter:
    """
    动态路由 — 根据任务类型选择调用主模型还是辅助模型。

    支持的任务类型:
      - "intent":      意图识别与消歧
      - "sql_validate": SQL 语法/逻辑验证
      - "explain":      结果自然语言解释
      - "sql_generate": SQL 生成（主模型专用，除非降级）
    """

    def __init__(self):
        self._clients = {}  # task -> AuxLLMClient 缓存

    def _get_client(self, task_type: str) -> AuxLLMClient:
        """获取或创建指定任务的辅助模型客户端"""
        if task_type not in self._clients:
            self._clients[task_type] = AuxLLMClient(task_type)
        return self._clients[task_type]

    def route(self, task_type: str, main_llm_callable=None, **kwargs) -> Any:
        """
        路由入口。

        参数:
            task_type: 任务类型
            main_llm_callable: 主模型调用函数（用于 SQL 生成等核心任务）
            **kwargs: 传递给各任务处理函数的参数

        返回:
            根据任务类型返回不同的数据结构
        """
        router_map = {
            "intent": self._handle_intent,
            "sql_validate": self._handle_sql_validate,
            "explain": self._handle_explain,
            "sql_generate": self._handle_sql_generate,
        }
        handler = router_map.get(task_type)
        if not handler:
            raise ValueError(f"未知的任务类型: {task_type}")
        return handler(main_llm_callable=main_llm_callable, **kwargs)

    # ------------------------------------------------------------------
    # 1. 意图识别与消歧
    # ------------------------------------------------------------------
    def _handle_intent(self, **kwargs) -> Dict[str, Any]:
        """
        判断用户问题是否清晰，是否需要反问澄清。

        返回:
            {
                "is_clear": bool,
                "clarification_needed": bool,
                "clarification_question": str,
                "missing_info": List[str],
            }
        """
        question = kwargs.get("question", "")
        schema_str = kwargs.get("schema_str", "")

        if not question.strip():
            return {"is_clear": False, "clarification_needed": True,
                    "clarification_question": "请描述您要查询什么数据？", "missing_info": []}

        prompt = INTENT_CLARIFICATION_PROMPT.format(
            user_question=question,
            schema_str=schema_str or "（无表结构）",
        )

        client = self._get_client("intent")
        result = client.generate_json(prompt)
        if result is None:
            logger.info("[Router] 意图识别: 辅助模型无响应，默认通过")
            return {"is_clear": True, "clarification_needed": False,
                    "clarification_question": "", "missing_info": []}

        logger.info(f"[Router] 意图识别: is_clear={result.get('is_clear')}, "
                    f"missing={result.get('missing_info', [])}")
        return result

    # ------------------------------------------------------------------
    # 2. SQL 语法/逻辑验证
    # ------------------------------------------------------------------
    def _handle_sql_validate(self, **kwargs) -> Dict[str, Any]:
        """
        检查生成的 SQL 是否存在语法或逻辑问题。

        返回:
            {
                "is_valid": bool,
                "issues": List[str],
                "suggestion": str,
            }
        """
        question = kwargs.get("question", "")
        sql = kwargs.get("sql", "")
        schema_str = kwargs.get("schema_str", "")
        db_type = kwargs.get("db_type", "sqlite")

        if not sql.strip():
            return {"is_valid": False, "issues": ["SQL 为空"], "suggestion": ""}

        prompt = SQL_VALIDATION_PROMPT.format(
            db_type=db_type,
            schema_str=schema_str or "（无表结构）",
            user_question=question,
            sql=sql,
        )

        client = self._get_client("sql_validate")
        result = client.generate_json(prompt)
        if result is None:
            logger.info("[Router] SQL验证: 辅助模型无响应，默认通过")
            return {"is_valid": True, "issues": [], "suggestion": ""}

        if not result.get("is_valid", True):
            logger.info(f"[Router] SQL验证: 发现 {len(result.get('issues', []))} 个问题: "
                        f"{result.get('issues', [])}")
        return result

    # ------------------------------------------------------------------
    # 3. 结果自然语言解释
    # ------------------------------------------------------------------
    def _handle_explain(self, **kwargs) -> str:
        """
        将查询结果转换成业务可读的自然语言描述。

        返回:
            解释文本（字符串）
        """
        question = kwargs.get("question", "")
        sql = kwargs.get("sql", "")
        result_rows = kwargs.get("result_rows", [])

        # 将结果格式化为文本（最多 10 行）
        if isinstance(result_rows, list) and len(result_rows) > 0:
            import pandas as pd
            try:
                df = pd.DataFrame(result_rows)
                rows_text = df.head(10).to_string(index=False)
                if len(result_rows) > 10:
                    rows_text += f"\n... 共 {len(result_rows)} 行"
            except Exception:
                rows_text = str(result_rows[:10])
        else:
            rows_text = "（无数据）"

        prompt = EXPLANATION_PROMPT.format(
            user_question=question,
            sql=sql,
            result_rows=rows_text,
        )

        client = self._get_client("explain")
        explanation = client.generate(prompt)
        if not explanation:
            logger.info("[Router] 解释: 辅助模型无响应")
            return ""

        logger.info(f"[Router] 解释完成: {len(explanation)} 字")
        return explanation

    # ------------------------------------------------------------------
    # 4. SQL 生成（降级模式 — 当主模型失败时）
    # ------------------------------------------------------------------
    def _handle_sql_generate(self, **kwargs) -> str:
        """
        降级模式：当主模型调用失败时，用辅助模型生成 SQL。

        返回:
            SQL 字符串
        """
        main_llm_callable = kwargs.get("main_llm_callable")
        if main_llm_callable:
            logger.info("[Router] 尝试主模型生成 SQL...")
            result = main_llm_callable()
            if result and not result.startswith("ERROR:"):
                return result

        logger.warning("[Router] 主模型失败，降级到辅助模型生成 SQL")
        # 用辅助模型重新生成
        question = kwargs.get("question", "")
        schema_str = kwargs.get("schema_str", "")
        try:
            from langchain_openai import ChatOpenAI
            client = self._get_client("sql_generate")
            llm = ChatOpenAI(
                model=client.model_name,
                temperature=0.2,
                max_tokens=500,
                api_key=client.api_key,
                base_url=client.base_url or None,
            )
            result = llm.invoke(kwargs.get("prompt", ""))
            return result.content if hasattr(result, 'content') else str(result)
        except Exception as e:
            logger.error(f"[Router] 降级生成 SQL 也失败: {e}")
            return f"ERROR: 所有模型均无法生成 SQL: {e}"


# ============================================================================
# 全局单例
# ============================================================================

_router_instance: Optional[ModelRouter] = None


def get_router() -> ModelRouter:
    """获取路由单例"""
    global _router_instance
    if _router_instance is None:
        _router_instance = ModelRouter()
    return _router_instance
