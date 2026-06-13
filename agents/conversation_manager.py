"""
==============================================================================
ConversationManager — 多轮对话上下文补全与记忆
==============================================================================
设计思路：
  用户在连续对话中经常使用省略句（如"那江苏呢？"、"按地区分一下"），
  这些语句缺少主语或表名，需要结合上一轮的查询上下文才能补全。

  本模块负责：
  1. 维护最近 5 轮的对话历史
  2. 用 llm_critic 将追问补全为可独立执行的完整查询句
  3. 从 SQL 中提取关键上下文（表名、过滤条件、聚合方式）

  补全后的完整查询句再交给 Router 分类，走正常查询流程。
==============================================================================
"""

import json
import logging
from typing import Dict, List, Optional

from core.llm_client import BaseLLMClient

logger = logging.getLogger("conversation_manager")

# ============================================================================
# 追问补全 Prompt
# ============================================================================

CONTEXT_COMPLETION_SYSTEM_PROMPT = """你是一名数据库查询意图补全专家。
用户在一轮对话中会连续提问，后面的问题经常省略上下文。
你需要根据上一轮的查询信息，将当前问题补全为可独立执行的完整查询句。

注意：
- 不要直接生成 SQL，只补全自然语言描述
- 保持原问题中的语气和指代
- 只需输出补全后的完整查询句，不要解释"""

CONTEXT_COMPLETION_PROMPT_TEMPLATE = """【上一轮查询】
用户问题：{last_query}
生成的 SQL：{last_sql}
查询上下文：
  主表：{last_table}
  过滤条件：{last_filters}
  聚合方式：{last_aggregation}
  分组方式：{last_group_by}
  排序方式：{last_order_by}

【用户当前输入】
{current_input}

【补全要求】
如果当前输入是省略句或指代句（如"那江苏呢？"、"按地区分一下"），
请结合上一轮上下文补全为完整的查询描述，以便系统理解完整意图。
如果当前输入已经是完整的查询句，原样输出即可。

只输出补全后的查询句，不要解释。"""


# ============================================================================
# 会话上下文提取 Prompt
# ============================================================================

CONTEXT_EXTRACTION_SYSTEM_PROMPT = """你是一名 SQL 分析专家。
从 SQL 语句中提取关键查询上下文，以 JSON 格式输出。

提取字段：
- "tables": 涉及的表名列表
- "filters": 过滤条件列表，每项包含 field、op、value
- "aggregation": 聚合方式，如 SUM、COUNT、AVG 等（无则为 null）
- "aggregation_field": 聚合字段名（无则为 null）
- "group_by": 分组字段列表
- "order_by": 排序字段和方向（无则为 null）
- "limit": 限制条数（无则为 null）

只输出 JSON，不要输出其他内容。"""


# ============================================================================
# ConversationManager
# ============================================================================

class ConversationManager:
    """
    多轮对话上下文管理器。

    职责：
    1. 将追问补全为完整查询句
    2. 维护最近 5 轮对话历史
    3. 从执行结果中提取上下文供后续使用

    用法:
        cm = ConversationManager(llm_critic)
        # 补全追问
        completed = cm.complete_query("那江苏呢？", last_context)
        # 更新历史
        cm.update_history(user_query, sql, result, context)
    """

    def __init__(self, llm_critic: BaseLLMClient):
        """
        初始化 ConversationManager。

        参数:
            llm_critic: 用于补全和提取上下文的 LLM 客户端
        """
        self.llm = llm_critic
        self.max_history = 5  # 最多保留最近 5 轮
        logger.info("[ConversationManager] 初始化完成")

    def complete_query(
        self,
        current_input: str,
        last_context: Optional[Dict] = None,
    ) -> str:
        """
        将用户的追问补全为完整查询句。

        如果当前输入已经是完整查询句（无法判断时），原样返回。

        参数:
            current_input: 用户当前输入
            last_context: 上一轮的查询上下文（为空时直接返回原句）

        返回:
            补全后的完整查询句
        """
        if not last_context:
            # 没有上下文，直接返回
            return current_input

        # 构建上一轮摘要
        last_query = last_context.get("user_query", "")
        last_sql = last_context.get("sql", "")
        last_table = ", ".join(last_context.get("tables", []))
        last_filters = self._format_filters(last_context.get("filters", []))
        last_aggregation = last_context.get("aggregation") or "无"
        last_group_by = ", ".join(last_context.get("group_by", [])) or "无"
        last_order_by = last_context.get("order_by") or "无"

        prompt = CONTEXT_COMPLETION_PROMPT_TEMPLATE.format(
            last_query=last_query or "无",
            last_sql=last_sql or "无",
            last_table=last_table or "无",
            last_filters=last_filters or "无",
            last_aggregation=last_aggregation,
            last_group_by=last_group_by,
            last_order_by=last_order_by,
            current_input=current_input,
        )

        try:
            completed = self.llm.generate(
                prompt=prompt,
                system_prompt=CONTEXT_COMPLETION_SYSTEM_PROMPT,
            )
            if completed:
                logger.info(
                    f"[ConversationManager] 追问补全: "
                    f"'{current_input}' → '{completed[:100]}'"
                )
                return completed.strip()
        except Exception as e:
            logger.warning(
                f"[ConversationManager] 补全失败，使用原句: {e}"
            )

        return current_input

    def extract_context(self, sql: str, user_query: str) -> Dict:
        """
        从 SQL 和用户查询中提取结构化上下文。

        参数:
            sql: 生成的 SQL 语句
            user_query: 用户原始问题

        返回:
            上下文字典，包含 tables、filters、aggregation 等字段
        """
        context = {
            "user_query": user_query,
            "sql": sql,
            "tables": [],
            "filters": [],
            "aggregation": None,
            "aggregation_field": None,
            "group_by": [],
            "order_by": None,
            "limit": None,
        }

        if not sql:
            return context

        # 先用 LLM 提取结构化上下文
        try:
            result = self.llm.generate_json(
                prompt=f"从以下 SQL 中提取查询上下文：\n```sql\n{sql}\n```\n",
                system_prompt=CONTEXT_EXTRACTION_SYSTEM_PROMPT,
            )
            if result:
                context.update({
                    k: result.get(k, context[k])
                    for k in context
                    if k != "user_query" and k != "sql"
                })
                logger.info(
                    f"[ConversationManager] 上下文提取: "
                    f"tables={context['tables']}, "
                    f"filters={context['filters']}"
                )
                return context
        except Exception as e:
            logger.debug(f"[ConversationManager] LLM 提取失败，使用正则: {e}")

        # 降级：用正则简单提取表名
        import re
        from_clause = re.search(
            r"\bFROM\s+`?(\w+)`?(?:\s+\w+)?", sql, re.IGNORECASE
        )
        if from_clause:
            context["tables"].append(from_clause.group(1))

        join_tables = re.findall(
            r"\bJOIN\s+`?(\w+)`?(?:\s+\w+)?", sql, re.IGNORECASE
        )
        context["tables"].extend(join_tables)

        return context

    def update_history(
        self,
        history: List[Dict],
        user_query: str,
        sql: str,
        context: Dict,
    ) -> List[Dict]:
        """
        更新对话历史，保留最近 N 轮。

        参数:
            history: 当前的对话历史列表
            user_query: 本次用户问题
            sql: 本次生成的 SQL
            context: 本次的查询上下文

        返回:
            更新后的历史列表（最多 self.max_history 轮）
        """
        entry = {
            "role": "user",
            "query": user_query,
            "sql": sql,
            "context": context,
        }
        history = list(history or [])
        history.append(entry)

        # 只保留最近 N 轮
        if len(history) > self.max_history:
            history = history[-self.max_history:]

        logger.info(
            f"[ConversationManager] 历史已更新 "
            f"({len(history)}/{self.max_history} 轮)"
        )
        return history

    def get_last_context(self, history: List[Dict]) -> Optional[Dict]:
        """
        从历史中获取上一轮的查询上下文。

        参数:
            history: 对话历史列表

        返回:
            最后一轮的上下文字典，无历史时返回 None
        """
        if not history:
            return None
        last = history[-1]
        return last.get("context")

    def _format_filters(self, filters: List) -> str:
        """格式化过滤条件为可读文本"""
        if not filters:
            return ""
        parts = []
        for f in filters:
            if isinstance(f, dict):
                parts.append(
                    f"{f.get('field', '?')} {f.get('op', '=')} "
                    f"{f.get('value', '?')}"
                )
            else:
                parts.append(str(f))
        return ", ".join(parts)
