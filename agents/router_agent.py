"""
==============================================================================
Router Agent — 用户意图分类与路由
==============================================================================
设计思路：
  Router Agent 是 LangGraph 流程的第一个节点。
  它对用户输入进行三分类：query（数据查询）、chat（闲聊）、dangerous（危险操作）。

  分类结果决定后续流程：
  - query → 进入 Schema 检索 + SQL 生成流水线
  - chat → 直接返回闲聊回复，不触发数据库
  - dangerous → 直接拒绝，记录审计日志

  若模型返回格式不正确，默认归类为 query（宁可多查，不可漏拦）。
==============================================================================
"""

import json
import logging
import re
from typing import Dict

from core.llm_client import BaseLLMClient

logger = logging.getLogger("router_agent")

# ============================================================================
# Router Agent 的 Prompt 模板
# ============================================================================

ROUTER_SYSTEM_PROMPT = """你是 Text-to-SQL 系统的意图分类器。
请判断用户输入的意图，只返回 JSON 格式，不要输出其他内容。

分类规则：
1. "query" — 用户需要查询数据库获取数据。包含：
   - 查询销售额、销量、用户数、订单量等数据指标
   - 询问统计数据、趋势、排名、对比
   - 提及表名、字段名、数据库相关内容
   - 示例："广东销售额"、"上个月订单量"、"哪个商品卖得最好"

2. "chat" — 日常对话，不需要查询数据库。包含：
   - 问候、寒暄、自我介绍
   - 询问系统能力、功能说明
   - 无关话题

3. "dangerous" — 可能尝试破坏数据库的操作。包含：
   - DDL 操作：创建/删除/修改表、索引、视图
   - DML 写操作：插入、更新、删除数据
   - 权限操作：授权、修改密码
   - 示例："删除所有订单"、"删表"、"清空数据"、"修改用户信息"

输出格式：
{"intent": "query|chat|dangerous", "reason": "简短说明分类理由"}

注意：不确定时归类为 "query"，让后续流程处理。
"""


class RouterAgent:
    """
    用户意图分类 Agent。

    对用户输入进行三分类路由，决定后续处理流程。

    用法:
        router = RouterAgent(llm_router)
        result = router.classify("广东销售额")
        # → {"intent": "query", "reason": "查询广东地区的销售额数据"}
    """

    def __init__(self, llm_client: BaseLLMClient):
        """
        初始化 Router Agent。

        参数:
            llm_client: 用于意图分类的 LLM 客户端实例
        """
        self.llm = llm_client
        logger.info("[RouterAgent] 初始化完成")

    def classify(self, user_input: str) -> Dict[str, str]:
        """
        对用户输入进行意图分类。

        参数:
            user_input: 用户的自然语言输入

        返回:
            {
                "intent": "query" | "chat" | "dangerous",
                "reason": "分类理由说明"
            }
        """
        if not user_input or not user_input.strip():
            logger.warning("[RouterAgent] 收到空输入，默认归类为 query")
            return {"intent": "query", "reason": "空输入，默认走查询流程"}

        logger.info(f"[RouterAgent] 分类输入: '{user_input[:100]}...'")

        try:
            raw_output = self.llm.generate(
                prompt=user_input,
                system_prompt=ROUTER_SYSTEM_PROMPT,
            )
            logger.info(f"[RouterAgent] 原始输出: {raw_output[:200]}")

            result = self._parse_response(raw_output)
            if result:
                logger.info(
                    f"[RouterAgent] 分类结果: intent={result['intent']}, "
                    f"reason={result['reason']}"
                )
                return result

        except Exception as e:
            logger.error(f"[RouterAgent] 调用失败: {e}")

        # 默认走 query 流程
        logger.warning("[RouterAgent] 解析失败，默认归类为 query")
        return {"intent": "query", "reason": "分类器解析失败，默认走查询流程"}

    def _parse_response(self, raw: str) -> Dict[str, str]:
        """
        解析模型返回的 JSON 结果。

        支持多种格式：
        - 标准 ```json ... ``` 代码块
        - 纯 JSON 字符串
        - 带前缀文本的 JSON

        参数:
            raw: 模型返回的原始文本

        返回:
            解析后的意图字典，解析失败返回 None
        """
        if not raw:
            return None

        # 提取 JSON 部分
        json_match = re.search(
            r"```(?:json)?\s*\n?(\{.*?\})\s*\n?```", raw, re.DOTALL
        )
        if json_match:
            raw = json_match.group(1)

        # 尝试直接解析
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # 查找第一个 { 和最后一个 }
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(raw[start : end + 1])
                except json.JSONDecodeError:
                    return None
            else:
                return None

        # 验证必要字段
        intent = data.get("intent", "").strip().lower()
        reason = data.get("reason", "").strip()

        if intent not in ("query", "chat", "dangerous"):
            logger.warning(
                f"[RouterAgent] 未知意图 '{intent}'，默认走 query 流程"
            )
            return {"intent": "query", "reason": reason or "未知意图，默认走查询流程"}

        return {"intent": intent, "reason": reason or "无说明"}
