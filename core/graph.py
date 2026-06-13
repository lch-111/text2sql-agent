"""
==============================================================================
LangGraph 状态图 — 多 Agent 协作的核心编排器
==============================================================================
设计思路：
  使用 LangGraph 定义有向状态图，编排以下节点：
    1. router_node → 意图分类
    2. chat_response_node / dangerous_reject_node → 非查询分支
    3. check_cache_node → L1/L2 缓存查询
    4. retrieve_schema_node → Schema 检索 + 精排
    5. generator_node → SQL 生成
    6. critic_node → SQL 校验（条件边：有效→执行，无效→回退生成）
    7. executor_node → SQL 执行（含物理安全拦截）
    8. write_cache_node → 写入缓存

  条件边实现分支逻辑：
  - router → 根据 intent 分 3 路
  - critic → 根据校验结果分 2 路
  - cache → 命中直接返回结果

  兼容性：保留原有 TextToSQLAgent.run() 接口签名，
          使 api/ 层无需改动即可使用新架构。
==============================================================================
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional, TypedDict

from core.config import CONFIG
from core.llm_client import BaseLLMClient

logger = logging.getLogger("graph")

# ============================================================================
# LangGraph 类型定义
# ============================================================================

try:
    from langgraph.graph import StateGraph, END

    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    logger.warning("[Graph] LangGraph 未安装，将使用简易模式运行")


class AgentState(TypedDict):
    """LangGraph 状态定义 — 所有节点的共享数据"""

    # 输入层
    user_input: str  # 用户原始输入
    normalized_input: str  # 术语替换后的输入
    completed_input: str  # 追问补全后的完整查询句
    history: List[Dict]  # 原始对话历史（前端传入）

    # 多轮对话上下文
    conversation_history: List[Dict]  # 结构化查询历史（最近 5 轮）
    last_query_context: Dict  # 上一轮的查询上下文
    is_follow_up: bool  # 当前是否为追问

    # Router 输出
    intent: str  # "query" | "chat" | "dangerous"
    intent_reason: str  # 路由分类理由

    # Schema 层
    schema_context: str  # 检索到的表结构文本
    term_hints: str  # 术语映射提示
    field_values: str  # 字段实际值提示

    # SQL 层
    sql: str  # 生成的 SQL

    # 缓存层
    cache_hit: bool  # 是否缓存命中
    cache_source: str  # "L1" | "L2" | ""

    # 执行结果
    result: List[Dict]  # 查询结果
    columns: List[str]  # 列名
    error: str  # 错误信息
    retries: int  # 已重试次数
    execution_time: float  # 总执行时间

    # 澄清
    clarification: str  # 需要澄清的问题（非空=需要澄清）

    # 额外信息
    messages: List[Dict]  # 节点日志消息

    # 记忆系统（增强）
    conv_id: str  # 对话 ID（前端传递，用于对话级记忆隔离）
    memory_context: Dict  # 记忆上下文缓存（减少重复检索）


def create_initial_state(
    user_input: str,
    history: list = None,
    conversation_history: list = None,
    conv_id: str = "",
) -> AgentState:
    """创建初始状态（含记忆系统字段）"""
    return {
        "user_input": user_input,
        "normalized_input": user_input,
        "completed_input": user_input,
        "history": history or [],
        "conversation_history": conversation_history or [],
        "last_query_context": {},
        "is_follow_up": False,
        "intent": "",
        "intent_reason": "",
        "schema_context": "",
        "term_hints": "",
        "field_values": "",
        "sql": "",
        "cache_hit": False,
        "cache_source": "",
        "result": [],
        "columns": [],
        "error": "",
        "retries": 0,
        "execution_time": 0.0,
        "clarification": "",
        "messages": [],
        "conv_id": conv_id,
        "memory_context": {},
    }


# ============================================================================
# Agent 与模型实例化
# ============================================================================

def _create_llm_clients():
    """根据配置创建所有 LLM 客户端实例"""
    cfg = CONFIG.llm

    # GLM 模型（Router / Critic / Reranker）→ 智谱 AI
    llm_router = BaseLLMClient(
        model=cfg.router_model,
        base_url=cfg.glm_base_url,
        api_key=cfg.glm_api_key,
        name="router",
    )
    # DeepSeek（Generator）→ 阿里云百炼
    llm_generator = BaseLLMClient(
        model=cfg.generator_model,
        base_url=cfg.openai_base_url,
        api_key=cfg.openai_api_key,
        name="generator",
        timeout=180,
    )
    llm_critic = BaseLLMClient(
        model=cfg.critic_model,
        base_url=cfg.glm_base_url,
        api_key=cfg.glm_api_key,
        name="critic",
    )
    llm_reranker = BaseLLMClient(
        model=cfg.reranker_model,
        base_url=cfg.glm_base_url,
        api_key=cfg.glm_api_key,
        name="reranker",
    )

    return llm_router, llm_generator, llm_critic, llm_reranker


def _create_agents():
    """创建所有 Agent 实例"""
    llm_router, llm_generator, llm_critic, llm_reranker = _create_llm_clients()

    from agents.router_agent import RouterAgent
    from agents.schema_retriever import SchemaRetriever
    from agents.generator_agent import GeneratorAgent
    from agents.critic_agent import CriticAgent
    from agents.executor_agent import ExecutorAgent
    from agents.conversation_manager import ConversationManager

    router = RouterAgent(llm_router)
    schema_retriever = SchemaRetriever(reranker_client=llm_reranker)
    generator = GeneratorAgent(llm_generator)
    critic = CriticAgent(
        llm_critic=llm_critic,
        llm_generator=llm_generator,
    )
    executor = ExecutorAgent()
    conversation_mgr = ConversationManager(llm_critic)

    return router, schema_retriever, generator, critic, executor, conversation_mgr


# ============================================================================
# 节点函数
# ============================================================================

def context_completion_node(state: AgentState) -> Dict:
    """
    上下文补全节点。

    调用 ConversationManager 将追问补全为完整查询句。
    例如用户问"那江苏呢？"，结合上一轮上下文补全为"查询江苏省的销售额"。

    补全后的语句写入 completed_input 字段，
    后续的 router_node 以 completed_input 作为分类依据。
    """
    _, _, _, _, _, conversation_mgr = _create_agents()
    start_time = __import__("time").time()

    user_input = state.get("user_input", "")
    conv_history = state.get("conversation_history", [])
    last_context = conversation_mgr.get_last_context(conv_history)

    if not last_context:
        return {"completed_input": user_input, "is_follow_up": False}

    completed = conversation_mgr.complete_query(user_input, last_context)
    is_follow_up = completed != user_input

    logger.info(
        f"[Graph] 上下文补全: "
        f"'{user_input}' -> '{completed[:120]}' "
        f"({'follow-up' if is_follow_up else 'independent'})"
    )

    return {
        "completed_input": completed,
        "is_follow_up": is_follow_up,
        "messages": [{
            "node": "context_completion",
            "duration": __import__("time").time() - start_time,
            "completed": completed,
        }],
    }


def save_context_node(state: AgentState) -> Dict:
    """
    上下文保存节点（增强版 — 触发记忆抽取）。

    查询执行成功后：
      1. 从 SQL 中提取结构化上下文（原有）
      2. 提取成功字段映射并存入 MemoryManager（新增）
      3. 记录查询偏好（新增）
      4. 高频规则自动提升为全局知识（新增）
    """
    _, _, _, _, _, conversation_mgr = _create_agents()

    sql = state.get("sql", "")
    user_query = (
        state.get("completed_input", "")
        or state.get("user_input", "")
    )
    conv_history = list(state.get("conversation_history", []))
    conv_id = state.get("conv_id", "")

    context = conversation_mgr.extract_context(sql, user_query)
    updated_history = conversation_mgr.update_history(
        history=conv_history,
        user_query=user_query,
        sql=sql,
        context=context,
    )

    # ---- 记忆抽取（隐私安全） ----
    try:
        from core.cache import get_cache
        _cache = get_cache()
        mm = getattr(_cache, "memory_manager", None)
        if mm and not state.get("error") and state.get("result"):
            # 1. 提取字段映射
            from agents.field_resolver import FieldResolver
            resolver = FieldResolver(memory_manager=mm)
            mappings = resolver.extract_mappings(
                query=user_query,
                sql=sql,
                conv_id=conv_id if conv_id else None,
            )
            for m in mappings:
                mm.set_global_mapping(
                    field=m["field"],
                    display_value=m["display_value"],
                    db_value=m["db_value"],
                )
                if conv_id:
                    mm.set_conv_mapping(
                        conv_id=conv_id,
                        field=m["field"],
                        display_value=m["display_value"],
                        db_value=m["db_value"],
                    )

            # 2. 记录过滤条件偏好
            filters = context.get("filters", [])
            if isinstance(filters, list):
                for f in filters:
                    if isinstance(f, dict) and "field" in f and "value" in f:
                        mm.record_conv_preference(
                            conv_id=conv_id if conv_id else "_default",
                            filter_key=str(f["field"]),
                            filter_value=str(f["value"]),
                        )
                        mm.add_global_preference(
                            filter_key=str(f["field"]),
                            filter_value=str(f["value"]),
                        )

            # 3. 对话级 → 全局提升
            if conv_id:
                promoted = mm.promote_to_global(conv_id)
                if promoted > 0:
                    logger.info(f"[Graph] 记忆提升: {promoted} 条规则从对话提升至全局")
    except Exception as e:
        logger.warning(f"[Graph] 记忆抽取异常（非致命）: {e}")

    return {
        "conversation_history": updated_history,
        "last_query_context": context,
    }

def router_node(state: AgentState) -> Dict:
    """
    意图分类节点。

    调用 RouterAgent 对用户输入分类。
    使用 completed_input（补全后的完整查询句）作为分类依据。
    根据意图决定后续分支：
    - query → 进入查询流水线
    - chat → 返回闲聊
    - dangerous → 直接拒绝
    """
    from agents.router_agent import RouterAgent

    router, _, _, _, _, _ = _create_agents()
    start = time.time()

    # 使用补全后的完整查询句进行分类
    query_to_classify = (
        state.get("completed_input", "")
        or state["user_input"]
    )
    result = router.classify(query_to_classify)

    return {
        "intent": result["intent"],
        "intent_reason": result["reason"],
        "messages": [
            {
                "node": "router",
                "duration": time.time() - start,
                "result": result,
            }
        ],
    }


def chat_response_node(state: AgentState) -> Dict:
    """
    闲聊响应节点。

    当意图为 chat 时，直接返回预设的闲聊回应。
    不触发任何数据库操作。
    """
    msg = (
        "您好！我是您的数据库智能分析助手。"
        "您可以问我关于数据的问题，比如：\n"
        "- 「上个月的销售额是多少？」\n"
        "- 「哪个商品卖得最好？」\n"
        "- 「各省份的用户分布情况」\n\n"
        "请问有什么数据需要我帮您查询？"
    )
    return {
        "result": [],
        "columns": [],
        "error": None,
        "clarification": msg,
    }


def dangerous_reject_node(state: AgentState) -> Dict:
    """
    危险操作拒绝节点。

    当意图为 dangerous 时，直接返回拒绝提示。
    安全审计日志在 SQLGuard 层记录。
    """
    msg = (
        "⚠️ 检测到您的输入可能涉及数据库写操作或结构变更。"
        "本系统仅支持 SELECT 查询，不支持 DDL/DML 操作。"
        "请提出数据查询类问题。"
    )
    logger.warning(
        f"[Graph] 危险操作被拒绝: '{state['user_input'][:100]}'"
    )
    return {
        "error": msg,
        "result": [],
        "columns": [],
    }


def check_cache_node(state: AgentState) -> Dict:
    """
    缓存检查节点。

    检查 L1 精确缓存和 L2 语义缓存。
    命中则跳过 SQL 生成。
    """
    from core.cache import get_cache

    start = time.time()

    try:
        cache = get_cache()
        cached = cache.get(state["normalized_input"])
        if cached:
            duration = time.time() - start
            logger.info(
                f"[Graph] 缓存命中 ({cached['source']}): "
                f"'{state['user_input'][:50]}...'"
            )
            return {
                "cache_hit": True,
                "cache_source": cached["source"],
                "sql": cached["sql"],
                "result": cached.get("result", []),
                "columns": (
                    list(cached["result"][0].keys())
                    if cached.get("result")
                    and isinstance(cached["result"][0], dict)
                    else []
                ),
                "messages": [
                    {
                        "node": "cache",
                        "duration": duration,
                        "result": "hit",
                    }
                ],
            }
    except Exception as e:
        logger.warning(f"[Graph] 缓存查询失败: {e}")

    return {
        "cache_hit": False,
        "cache_source": "",
    }


def retrieve_schema_node(state: AgentState) -> Dict:
    """
    Schema 检索节点。

    并行执行 Schema 检索和字段值加载，减少串行等待时间。
    """
    import concurrent.futures
    from agents.schema_retriever import SchemaRetriever

    _, schema_retriever, _, _, _, _ = _create_agents()
    start = time.time()

    query_for_retrieval = (
        state.get("completed_input", "")
        or state["user_input"]
    )

    # 并行执行 Schema 搜索 + Rerank 和字段值加载
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        future_schema = executor.submit(schema_retriever.build_schema_text, query_for_retrieval)
        future_values = executor.submit(schema_retriever.load_field_values)

        schema_text = future_schema.result()
        field_values = future_values.result()

    duration = time.time() - start
    logger.info(f"[Graph] Schema 检索完成 ({duration:.2f}s)")

    return {
        "normalized_input": query_for_retrieval,
        "schema_context": schema_text,
        "field_values": field_values,
        "messages": [
            {"node": "schema_retrieve", "duration": duration}
        ],
    }


def build_memory_context_node(state: AgentState) -> Dict:
    """
    记忆上下文构建节点。

    在 generator 之前执行，检索全局 + 对话级记忆，
    将字段映射、Trace 提示、偏好提示组装为 memory_context，
    供 generator_node 注入 Prompt。
    """
    from core.cache import get_cache

    try:
        cache = get_cache()
        mm = cache.memory_manager
        conv_id = state.get("conv_id", "")

        ctx = mm.build_memory_context(
            query=state.get("normalized_input", "") or state.get("user_input", ""),
            conv_id=conv_id if conv_id else None,
        )
        logger.info(
            f"[Graph] 记忆上下文构建完成: "
            f"field_hints={bool(ctx['field_hints'])}, "
            f"trace_hints={bool(ctx['trace_hints'])}, "
            f"preference_hints={bool(ctx['preference_hints'])}"
        )
        return {"memory_context": ctx}
    except Exception as e:
        logger.warning(f"[Graph] 记忆上下文构建失败: {e}")
        return {"memory_context": {}}


def generator_node(state: AgentState) -> Dict:
    """
    SQL 生成节点。

    调用 GeneratorAgent 根据 Schema 上下文生成 SQL。
    """
    from agents.generator_agent import GeneratorAgent

    _, _, generator, _, _, _ = _create_agents()
    start = time.time()

    memory_ctx = state.get("memory_context", {})
    result = generator.generate(
        query=state["normalized_input"],
        schema_context=state["schema_context"],
        resolved_fields=memory_ctx.get("field_hints", ""),
        resolved_values="",  # 保留为空，由 FieldResolver 填充
        trace_hints=memory_ctx.get("trace_hints", ""),
        preference_hints=memory_ctx.get("preference_hints", ""),
    )
    sql = result["sql"] if isinstance(result, dict) else result
    reasoning = result.get("reasoning", "") if isinstance(result, dict) else ""

    duration = time.time() - start
    logger.info(f"[Graph] SQL 生成完成 ({duration:.2f}s)")

    # 组装思维链：SQL 生成推理（过滤 Router 内部错误信息）
    router_reason = state.get("intent_reason", "")
    thinking_parts = []
    # 过滤"分类器解析失败"等内部降级消息
    if router_reason and "分类器" not in router_reason and "降级" not in router_reason:
        thinking_parts.append(f"🔍 意图识别：{router_reason}")
    if reasoning:
        thinking_parts.append(f"💡 {reasoning}")
    thinking = "\n\n".join(thinking_parts) if thinking_parts else "已为你生成 SQL"

    return {
        "sql": sql,
        "thinking": thinking,
        "messages": [{"node": "generate", "duration": duration, "reasoning": reasoning[:500]}],
    }


def critic_node(state: AgentState) -> Dict:
    """
    SQL 校验节点。

    调用 CriticAgent 验证 SQL 语法和逻辑。
    校验结果决定后续边：
    - 有效 → 进入 executor_node
    - 无效 → 回退到 generator_node 重新生成
    """
    from agents.critic_agent import CriticAgent

    _, _, _, critic, _, _ = _create_agents()

    is_valid, issues = critic.validate_syntax(
        sql=state["sql"],
        schema_context=state["schema_context"],
    )

    logger.info(
        f"[Graph] SQL 校验: {'通过' if is_valid else '不通过'} "
        f"{issues}"
    )

    return {
        "messages": [
            {
                "node": "critic",
                "result": "valid" if is_valid else "invalid",
                "issues": issues,
            }
        ],
    }


def executor_node(state: AgentState) -> Dict:
    """
    SQL 执行节点。

    调用 ExecutorAgent 执行 SQL（含 SQLGuard 物理安全拦截）。
    """
    from agents.executor_agent import ExecutorAgent

    _, _, _, _, executor, _ = _create_agents()
    start = time.time()

    result = executor.execute_with_result(state["sql"])

    duration = time.time() - start
    logger.info(f"[Graph] SQL 执行完成 ({duration:.2f}s)")

    return {
        "result": result.get("result", []),
        "columns": result.get("columns", []),
        "error": result.get("error"),
        "execution_time": duration,
        "messages": [
            {
                "node": "execute",
                "duration": duration,
                "row_count": result.get("row_count", 0),
            }
        ],
    }


def self_correction_node(state: AgentState) -> Dict:
    """
    自我修正节点。

    SQL 执行失败时，调用 CriticAgent 的 self_correction_loop 生成修正 SQL。
    修正后重新进入 generator_node 之前的流程。
    """
    from agents.critic_agent import CriticAgent

    _, _, _, critic, _, _ = _create_agents()

    logger.info(
        f"[Graph] 触发自我修正 (第 {state['retries'] + 1} 次)"
    )

    from core.cache import get_cache
    _cache = get_cache()
    _mm = getattr(_cache, "memory_manager", None)

    corrected_sql = critic.self_correction_loop(
        original_query=state["normalized_input"],
        failed_sql=state["sql"],
        error_message=state["error"],
        schema_context=state["schema_context"],
        conv_id=state.get("conv_id", ""),
        memory_manager=_mm,
    )

    if corrected_sql:
        logger.info(f"[Graph] 自我修正成功")
        return {
            "sql": corrected_sql,
            "retries": state["retries"] + 1,
            "error": "",
        }
    else:
        logger.warning(f"[Graph] 自我修正失败")
        return {
            "retries": state["retries"] + 1,
            "error": "抱歉，我暂时无法完成这个查询，请联系管理员。",
        }


def write_cache_node(state: AgentState) -> Dict:
    """
    缓存写入节点。

    查询成功后，将结果写入 L1 和 L2 缓存。
    """
    from core.cache import get_cache

    if not state["error"] and state["result"]:
        try:
            cache = get_cache()
            from core.config import CONFIG
            token_est = len(state["sql"]) * 2
            cache.set(
                query=state["normalized_input"],
                sql=state["sql"],
                result=state["result"],
                token_estimate=token_est,
            )
            logger.info("[Graph] 缓存写入完成")
        except Exception as e:
            logger.warning(f"[Graph] 缓存写入失败: {e}")

    return {}


# ============================================================================
# 条件路由函数
# ============================================================================

def router_intent_router(state: AgentState) -> str:
    """根据意图决定后续节点"""
    intent = state.get("intent", "")
    if intent == "chat":
        return "chat"
    elif intent == "dangerous":
        return "dangerous"
    else:
        return "query"


def cache_router(state: AgentState) -> str:
    """根据缓存命中决定后续节点"""
    if state.get("cache_hit"):
        return "hit"
    return "miss"


def critic_router(state: AgentState) -> str:
    """
    根据 Critic 校验结果 + 当前错误状态决定后续节点。

    规则：
    - 如果 critc 发现 SQL 无效且重试次数未超限 → 回退到 generator_node
    - 如果执行失败且重试次数未超限 → 进入 self_correction_node
    - 其他情况 → 进入 executor_node 或 END
    """
    messages = state.get("messages", [])
    critic_msgs = [
        m for m in messages if m.get("node") == "critic"
    ]

    # 检查 Critic 是否报告了问题
    has_critic_issues = any(
        m.get("result") == "invalid" for m in critic_msgs
    )

    # 检查是否有执行错误且未超限
    has_error = bool(state.get("error"))
    retries = state.get("retries", 0)
    can_retry = retries < 2

    if has_critic_issues and can_retry:
        return "regenerate"
    elif has_error and can_retry:
        return "self_correct"
    else:
        return "execute"


# ============================================================================
# 构建 LangGraph
# ============================================================================

_compiled_graph = None


def build_graph():
    """
    构建并编译 LangGraph 状态图。

    节点图结构：
    context_completion → router → (chat|dangerous|query)
    query → check_cache → (hit: save_context | miss: retrieve_schema)
    retrieve_schema → generator → critic
    critic → (valid: executor | invalid: generator [loop])
    executor → (success: write_cache → save_context → END
                 | fail: self_correction)
    self_correction → (success: executor | fail: END)
    """
    if not LANGGRAPH_AVAILABLE:
        logger.warning("[Graph] LangGraph 不可用，返回空图")
        return None

    workflow = StateGraph(AgentState)

    # ---- 注册节点 ----
    workflow.add_node("context_completion", context_completion_node)
    workflow.add_node("router", router_node)
    workflow.add_node("chat_response", chat_response_node)
    workflow.add_node("dangerous_reject", dangerous_reject_node)
    workflow.add_node("check_cache", check_cache_node)
    workflow.add_node("retrieve_schema", retrieve_schema_node)
    workflow.add_node("build_memory_context", build_memory_context_node)
    workflow.add_node("generator", generator_node)
    workflow.add_node("critic", critic_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("self_correction", self_correction_node)
    workflow.add_node("write_cache", write_cache_node)
    workflow.add_node("save_context", save_context_node)

    # ---- 设置入口 ----
    workflow.set_entry_point("context_completion")

    # ---- 固定边：上下文补全 → Router ----
    workflow.add_edge("context_completion", "router")

    # ---- 条件边：Router ----
    workflow.add_conditional_edges(
        "router",
        router_intent_router,
        {
            "chat": "chat_response",
            "dangerous": "dangerous_reject",
            "query": "check_cache",
        },
    )

    # ---- 条件边：Cache ----
    workflow.add_conditional_edges(
        "check_cache",
        cache_router,
        {"hit": "save_context", "miss": "retrieve_schema"},
    )

    # ---- 固定边：Schema 检索 → 记忆上下文 → 生成 ----
    workflow.add_edge("retrieve_schema", "build_memory_context")
    workflow.add_edge("build_memory_context", "generator")

    # ---- 条件边：Critic ----
    workflow.add_conditional_edges(
        "generator",
        critic_router,
        {
            "regenerate": "generator",  # 回退重新生成
            "self_correct": "self_correction",
            "execute": "executor",
        },
    )

    # ---- 固定边：自我修正 → 再次执行 ----
    workflow.add_edge("self_correction", "executor")

    # ---- 固定边：执行 → 写入缓存 → 保存上下文 ----
    workflow.add_edge("executor", "write_cache")
    workflow.add_edge("write_cache", "save_context")

    # ---- 终止节点 ----
    workflow.add_edge("chat_response", END)
    workflow.add_edge("dangerous_reject", END)
    workflow.add_edge("save_context", END)

    # ---- 编译 ----
    compiled = workflow.compile()
    logger.info("[Graph] LangGraph 状态图编译完成")
    return compiled


def get_graph():
    """
    获取编译后的 LangGraph 实例（单例）。

    返回:
        CompiledStateGraph 或 None（LangGraph 不可用时）
    """
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


# ============================================================================
# 简易模式 — 当 LangGraph 不可用时的降级运行
# ============================================================================

def run_simple(state: AgentState) -> Dict[str, Any]:
    """
    简易模式：串行执行所有 Agent，不用 LangGraph。

    用于：
    - LangGraph 未安装时的降级
    - 单元测试

    参数:
        state: 初始状态

    返回:
        最终结果字典（兼容原来的 get_agent().run() 返回格式）
    """
    start_time = time.time()

    # ---- 上下文补全（追问处理）----
    completion_result = context_completion_node(state)
    state.update(completion_result)

    # ---- Router ----
    router_result = router_node(state)
    state.update(router_result)
    intent = state.get("intent", "")

    if intent == "chat":
        chat_result = chat_response_node(state)
        state.update(chat_result)
        return _format_result(state, start_time)

    if intent == "dangerous":
        reject_result = dangerous_reject_node(state)
        state.update(reject_result)
        return _format_result(state, start_time)

    # ---- 缓存检查 ----
    cache_result = check_cache_node(state)
    state.update(cache_result)
    if state.get("cache_hit"):
        # 缓存命中也要保存上下文（供后续追问使用）
        save_context_node(state)
        return _format_result(state, start_time)

    # ---- Schema 检索 ----
    schema_result = retrieve_schema_node(state)
    state.update(schema_result)

    # ---- 记忆上下文构建 ----
    memory_result = build_memory_context_node(state)
    state.update(memory_result)

    # ---- SQL 生成（带自我修正循环） ----
    max_retries = 2
    for attempt in range(max_retries + 1):
        gen_result = generator_node(state)
        state.update(gen_result)

        # Critic 校验
        critic_result = critic_node(state)
        state.update(critic_result)

        # 检查 critic 结果
        critic_msgs = [
            m
            for m in state.get("messages", [])
            if m.get("node") == "critic"
        ]
        if any(m.get("result") == "invalid" for m in critic_msgs):
            if attempt < max_retries:
                # 尝试自我修正
                corr_result = self_correction_node(state)
                state.update(corr_result)
                if state.get("error"):
                    break
                continue
            else:
                state["error"] = "SQL 校验不通过，请换个问法"
                break

        # ---- 执行 SQL ----
        exec_result = executor_node(state)
        state.update(exec_result)

        if state.get("error") and attempt < max_retries:
            # 执行失败，自我修正后重试
            corr_result = self_correction_node(state)
            state.update(corr_result)
            if state.get("error"):
                continue
            else:
                # 重新执行修正后的 SQL
                exec_result = executor_node(state)
                state.update(exec_result)
                break
        else:
            break

    # ---- 写入缓存 + 保存上下文 ----
    if not state.get("error") and state.get("result"):
        try:
            write_cache_node(state)
        except Exception:
            pass
    # 无论缓存是否写入，都保存对话上下文（供后续追问使用）
    try:
        save_context_node(state)
    except Exception:
        pass

    return _format_result(state, start_time)


def _format_result(state: AgentState, start_time: float) -> Dict[str, Any]:
    """将 AgentState 格式化为与原来 get_agent().run() 兼容的字典"""
    return {
        "question": state.get("user_input", ""),
        "sql": state.get("sql"),
        "result": state.get("result") if state.get("result") not in (None, []) else (state.get("result") or []),
        "columns": state.get("columns") or [],
        "error": state.get("error"),
        "clarification": state.get("clarification", ""),
        "cache_hit": state.get("cache_hit", False),
        "cache_source": state.get("cache_source"),
        "retries": state.get("retries", 0),
        "execution_time": time.time() - start_time,
        # 多轮对话上下文（前端在后续请求中原样送回）
        "conversation_history": state.get("conversation_history", []),
        "is_follow_up": state.get("is_follow_up", False),
        "completed_input": state.get("completed_input", ""),
        # 思维链与中间过程
        "intent_reason": state.get("intent_reason", ""),
        "messages": state.get("messages", []),
        "thinking": state.get("thinking") or state.get("intent_reason", ""),  # 思维链（优先取 sql_generate_node 组装的 thinking）
    }


# ============================================================================
# 统一执行入口
# ============================================================================

def execute(
    question: str,
    history: list = None,
    conversation_history: list = None,
    conv_id: str = "",
) -> Dict[str, Any]:
    """
    统一执行入口（增强版 — 支持记忆系统）。

    优先使用 LangGraph 图编排，不可用时降级到简易模式。

    参数:
        question: 用户问题
        history: 原始对话历史（前端传入）
        conversation_history: 结构化查询历史（跨轮追问用）
        conv_id: 对话 ID（用于对话级记忆隔离）

    返回:
        结果字典（与原有 get_agent().run() 兼容）
    """
    state = create_initial_state(
        user_input=question,
        history=history,
        conversation_history=conversation_history,
        conv_id=conv_id,
    )

    graph = get_graph()
    if graph is not None:
        try:
            logger.info("[Graph] 使用 LangGraph 执行")
            result = graph.invoke(state)
            return _format_result(result, time.time())
        except Exception as e:
            logger.error(f"[Graph] LangGraph 执行失败，降级到简易模式: {e}")

    logger.info("[Graph] 使用简易模式执行")
    return run_simple(state)
