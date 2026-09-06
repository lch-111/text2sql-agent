"""
==============================================================================
model_client — 统一模型调用层（所有模型入口在此收口）
==============================================================================
改造背景：
  改造前项目存在多条模型调用路径：graph 里 Router/Critic/Reranker 走 GLM
  配置、Generator 走百炼/OpenAI、api/routes.py 的四个端点各自裸拼
  BaseLLMClient，api_key 来源混乱，用户需要同时维护多把 Key。

  本模块把「角色→模型选择 + Key 解析 + 端点解析」收口到 create_llm_client
  一处，并按【模型名前缀】自动路由到对应的 Key 槽位：
    - deepseek* → DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL
    - qwen*     → DASHSCOPE_API_KEY（无独立端点变量，固定百炼兼容端点）
    - glm*      → GLM_API_KEY / GLM_BASE_URL
  说明：qwen 与 deepseek 均上架阿里云百炼（DashScope 兼容端点），对单把
  百炼 Key 的存量配置而言二者共用同一 Key——前缀路由只是“优先挑对应槽位”，
  某槽位未配置 Key 时自动沿 DEEPSEEK→DASHSCOPE→OPENAI→GLM 回退，绝不因
  缺一把 Key 而断供。

设计原则：
  1. 只替换“构建客户端”这一层；LangGraph 三段式流程、SQL 生成、SQLGuard
     等核心逻辑一律不碰（引擎无关）。
  2. 端点解析跟随“被选中 Key 所属槽位”的 base_url；各槽位默认均指百炼
     兼容端点（见 core/config.LLMConfig / DASHSCOPE_COMPAT_URL）。
  3. 角色模型分级（generator/critic 用 deepseek pro，router/reranker 用
     qwen flash），每个角色仍可用对应的 *_MODEL 环境变量覆盖，默认值见
     core/config.py。
==============================================================================
"""

import logging
from typing import Optional, Tuple

from core.config import CONFIG, DASHSCOPE_COMPAT_URL
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


# ============================================================================
# Key 槽位表 + 按模型名前缀的路由
# ============================================================================

# provider 槽位表：(provider 名, LLMConfig 上的 API_KEY 字段, BASE_URL 字段)
#   base_url 字段为 None 表示该槽位固定使用百炼兼容端点（dashscope 无独立端点变量）。
_PROVIDER_SLOTS: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("deepseek", "deepseek_api_key", "deepseek_base_url"),
    ("dashscope", "dashscope_api_key", None),
    ("openai", "openai_api_key", "openai_base_url"),
    ("glm", "glm_api_key", "glm_base_url"),
)
_PROVIDER_BY_NAME: dict = {slot[0]: slot for slot in _PROVIDER_SLOTS}

# 全局“空 Key”回退顺序（与历史 resolved_* 一致：DEEPSEEK → DASHSCOPE → OPENAI → GLM）。
_KEY_FALLBACK_ORDER: Tuple[str, ...] = ("deepseek", "dashscope", "openai", "glm")

# 模型名前缀 → 首选 provider（按出现顺序匹配，模型名一般以家族名开头）。
#   qwen 属阿里百炼，故首选 dashscope 槽；deepseek-v4-pro-0813 也上架百炼，
#   首选其专属 deepseek 槽（deepseek_base_url 默认即百炼端点），空则回退。
_MODEL_PREFIX_PROVIDER: Tuple[Tuple[str, str], ...] = (
    ("deepseek", "deepseek"),
    ("qwen", "dashscope"),
    ("glm", "glm"),
)


def resolve_for_model(model: str) -> Tuple[str, str, str]:
    """按模型名解析出 (api_key, base_url, provider)。

    路由规则：
      1. 取模型名前缀在 _MODEL_PREFIX_PROVIDER 中命中首选 provider（deepseek/qwen/glm）；
      2. 从首选槽开始，沿 _KEY_FALLBACK_ORDER 依次取“第一个已配置 Key”的槽位；
      3. 前缀无匹配（未知模型）时，直接从全局回退顺序取首个非空 Key 槽；
      4. 所有槽位都空 → 返回空 Key + 百炼兼容端点（供上层报错定位，不抛异常）。

    参数:
        model: 完整模型名，如 "qwen3.8-flash" / "deepseek-v4-pro-0813" / "glm-4-flash"

    返回:
        (api_key, base_url, provider) 三元组；api_key 可能为空串（全部未配置时）。
    """
    cfg = CONFIG.llm
    name = (model or "").lower()

    # 1) 按模型名前缀找首选 provider
    preferred: Optional[str] = None
    for prefix, prov in _MODEL_PREFIX_PROVIDER:
        if name.startswith(prefix):
            preferred = prov
            break

    # 2) 构造槽位尝试顺序：首选在前，其余保持全局回退顺序
    order = _KEY_FALLBACK_ORDER
    if preferred and preferred in order:
        order = (preferred,) + tuple(p for p in order if p != preferred)

    # 3) 取第一个非空 Key 的槽位
    for prov in order:
        slot = _PROVIDER_BY_NAME[prov]
        key = (getattr(cfg, slot[1]) or "").strip()
        if not key:
            continue
        if slot[2]:  # 有独立 base_url 字段 → 用其配置值，空则回退百炼端点
            base = (getattr(cfg, slot[2]) or DASHSCOPE_COMPAT_URL).rstrip("/")
        else:        # dashscope 槽位固定百炼兼容端点
            base = DASHSCOPE_COMPAT_URL
        return key, base, prov

    # 4) 全部未配置
    return "", DASHSCOPE_COMPAT_URL, "none"


def _mask_key(key: str) -> str:
    """Key 脱敏：保留前 6 位与后 4 位，中间用 ***（空串原样返回）。"""
    if not key:
        return ""
    return key[:6] + "***" + key[-4:]


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
        api_key / base_url 由 resolve_for_model 按模型名前缀路由得到
        （qwen/deepseek 均可用同一把百炼 Key，槽位为空时自动回退）。

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
    api_key, base_url, provider = resolve_for_model(model)

    # 客户端默认按角色放行超时；显式 timeout 覆盖角色默认
    effective_timeout = (
        timeout if timeout is not None else _ROLE_TIMEOUT.get(role)
    )

    client = BaseLLMClient(
        model=model,
        base_url=base_url,
        api_key=api_key,
        name=name or role,
    )
    if effective_timeout is not None:
        client.timeout = effective_timeout

    logger.info(
        "[model_client] 创建客户端 role=%s name=%s model=%s provider=%s key=%s timeout=%s",
        role,
        client.name,
        model,
        provider,
        _mask_key(api_key),
        client.timeout,
    )
    return client


def resolved_summary() -> dict:
    """返回模型解析摘要（用于启动诊断，Key 一律脱敏）。

    返回:
        {
          "key_slots": {provider: bool},      # 哪些 Key 槽位已配置
          "routes": {role: {model, provider, base_url, key_masked}},  # 逐角色路由结果
        }
    """
    cfg = CONFIG.llm
    key_slots = {
        slot[0]: bool((getattr(cfg, slot[1]) or "").strip())
        for slot in _PROVIDER_SLOTS
    }
    routes = {}
    for role, attr in sorted(_ROLE_MODEL_ATTR.items()):
        model = getattr(cfg, attr)
        api_key, base_url, provider = resolve_for_model(model)
        routes[role] = {
            "model": model,
            "provider": provider,
            "base_url": base_url,
            "key_masked": _mask_key(api_key),
        }
    return {"key_slots": key_slots, "routes": routes}
