"""
==============================================================================
model_client — 统一模型调用层（所有模型入口在此收口）
==============================================================================
改造背景：
  改造前项目存在多条模型调用路径：graph 里 Router/Critic/Reranker 走 GLM
  配置、Generator 走百炼/OpenAI、api/routes.py 的四个端点各自裸拼
  BaseLLMClient，api_key 来源混乱，用户需要同时维护多把 Key。

  本模块把「角色→模型选择 + Key 解析 + 端点解析」收口到 create_llm_client
  一处。此后用户只需配置一个 DEEPSEEK_API_KEY，其余自动推导。

设计原则：
  1. 只替换“构建客户端”这一层；LangGraph 三段式流程、SQL 生成、SQLGuard
     等核心逻辑一律不碰（引擎无关）。
  2. Key 解析顺序：DEEPSEEK_API_KEY → DASHSCOPE_API_KEY → OPENAI_API_KEY
     → GLM_API_KEY（存量 .env 无缝兼容，不需要用户迁移）。
  3. 角色模型分级（generator/critic 用 pro，router/reranker 用 flash），
     每个角色仍可用对应的 *_MODEL 环境变量覆盖，默认值见 core/config.py。
  4. 端点解析跟随“被选中的 Key”所属提供商，见 core/config.LLMConfig.
==============================================================================
"""

import logging
from typing import Optional

from core.config import CONFIG
from core.llm_client import BaseLLMClient

logger = logging.getLogger("model_client")

# 角色 → core/config.LLMConfig 中对应的模型字段名。
# tool_selector（ReAct 工具选择，轻量分类任务）与 router 共用一套模型配置，
# 避免为它单独新增一个环境变量。
_ROLE_MODEL_ATTR: dict = {
    "router": "router_model",
    "generator": "generator_model",
    "critic": "critic_model",
    "reranker": "reranker_model",
    "tool_selector": "router_model",
}

# 各角色的请求超时（秒）：None 表示交给 BaseLLMClient 默认 120s。
# generator 承担最长链路的 SQL 生成，历史实现显式放大到 180s，这里保持一致。
_ROLE_TIMEOUT: dict = {
    "generator": 180,
    "tool_selector": 180,  # ReAct 单轮可能内嵌多次推理，放宽超时
}


def create_llm_client(
    role: str,
    name: Optional[str] = None,
    timeout: Optional[int] = None,
) -> BaseLLMClient:
    """
    按角色创建统一 LLM 客户端（唯一推荐入口）。

    参数:
        role: 角色名，取值 router / generator / critic / reranker / tool_selector
        name: 客户端标识（用于日志区分），缺省取 role
        timeout: 请求超时（秒），显式传入时优先生效；None 则用角色默认

    返回:
        已完成 model / api_key / base_url 解析的 BaseLLMClient 实例，
        model 来自 CONFIG.llm 对应字段（支持 *_MODEL 环境变量覆盖），
        api_key / base_url 来自 CONFIG.llm 的统一解析结果（DeepSeek 优先）。

    异常:
        ValueError: role 不在白名单内，避免拼错角色名而静默用错模型。
    """
    attr = _ROLE_MODEL_ATTR.get(role)
    if attr is None:
        raise ValueError(
            f"未知模型角色: {role!r}，可选: {sorted(_ROLE_MODEL_ATTR)}"
        )

    cfg = CONFIG.llm
    model = getattr(cfg, attr)

    # 客户端默认按角色放行超时；显式 timeout 覆盖角色默认
    effective_timeout = (
        timeout if timeout is not None else _ROLE_TIMEOUT.get(role)
    )

    client = BaseLLMClient(
        model=model,
        base_url=cfg.resolved_base_url,
        api_key=cfg.resolved_api_key,
        name=name or role,
    )
    if effective_timeout is not None:
        client.timeout = effective_timeout

    logger.info(
        "[model_client] 创建客户端 role=%s name=%s model=%s provider=%s timeout=%s",
        role,
        client.name,
        model,
        cfg.resolved_provider,
        client.timeout,
    )
    return client


def resolved_summary() -> dict:
    """返回模型解析摘要（用于启动诊断，Key 一律脱敏）。

    返回:
        {"provider", "model_routes": {role: model}, "key_masked"}
    """
    cfg = CONFIG.llm
    key = cfg.resolved_api_key
    return {
        "provider": cfg.resolved_provider,
        "key_masked": (key[:6] + "***" + key[-4:]) if key else "",
        "models": {
            role: getattr(cfg, _ROLE_MODEL_ATTR[role])
            for role in sorted(_ROLE_MODEL_ATTR)
        },
        "base_url": cfg.resolved_base_url,
    }
