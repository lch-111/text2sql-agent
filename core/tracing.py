"""
==============================================================================
LangSmith 链路追踪模块 — 全流程可观测性
==============================================================================
设计思路：
  在 Text-to-SQL 场景中，链路追踪是调试和优化的关键。
  通过 LangSmith 可以追踪：
    1. Schema 检索（混合检索的召回率和排序质量）
    2. Prompt 构造（注入的上下文是否正确）
    3. LLM 调用（生成延迟和 Token 消耗）
    4. SQL 执行（执行时间和结果行数）
    5. 缓存命中（L1/L2 命中率分析）

  本模块封装了 LangSmith 的初始化、追踪上下文管理器和装饰器。
  可通过环境变量一键启用/禁用。

  环境变量配置：
    LANGSMITH_API_KEY=lsv2_...
    LANGSMITH_PROJECT=text2sql-agent
    LANGSMITH_TRACING=true
    LANGSMITH_ENDPOINT=https://api.smith.langchain.com
==============================================================================
"""

import functools
import logging
import os
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("tracing")


# ============================================================================
# LangSmith 初始化
# ============================================================================

# 全局追踪状态
_tracing_enabled = False
_tracing_initialized = False
_langsmith_run_id = None


def init_tracing(
    project_name: Optional[str] = None,
    api_key: Optional[str] = None,
    endpoint: Optional[str] = None,
    tracing_enabled: Optional[bool] = None,
) -> bool:
    """
    初始化 LangSmith 追踪。

    优先从配置文件（config.py）读取，支持按需覆盖。

    环境变量:
        LANGSMITH_API_KEY: LangSmith API Key
        LANGSMITH_PROJECT: 项目名称（默认 text2sql-agent）
        LANGSMITH_TRACING: 是否启用追踪（true/false）
        LANGSMITH_ENDPOINT: LangSmith API 地址

    返回:
        是否成功启用追踪
    """
    global _tracing_enabled, _tracing_initialized

    if _tracing_initialized:
        return _tracing_enabled

    # 优先从 config.py 读取
    try:
        from core.config import CONFIG
        cfg = CONFIG.tracing
        env_key = api_key or cfg.api_key or os.getenv("LANGSMITH_API_KEY", "")
        env_project = project_name or cfg.project or os.getenv("LANGSMITH_PROJECT", "text2sql-agent")
        env_tracing = tracing_enabled if tracing_enabled is not None else (cfg.enabled or os.getenv("LANGSMITH_TRACING", "false").lower() == "true")
        env_endpoint = endpoint or cfg.endpoint or os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    except Exception:
        # 兜底：直接从环境变量读取
        env_key = api_key or os.getenv("LANGSMITH_API_KEY", "")
        env_project = project_name or os.getenv("LANGSMITH_PROJECT", "text2sql-agent")
        env_tracing = tracing_enabled if tracing_enabled is not None else (os.getenv("LANGSMITH_TRACING", "false").lower() == "true")
        env_endpoint = endpoint or os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")

    # 判断是否启用
    should_enable = env_tracing and bool(env_key)

    if not should_enable:
        _tracing_initialized = True
        _tracing_enabled = False
        logger.info("[Tracing] LangSmith 未启用（未配置 API Key 或未启用追踪）")
        return False

    # 设置 LangChain 环境变量
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_API_KEY"] = env_key
    os.environ["LANGCHAIN_PROJECT"] = env_project
    os.environ["LANGCHAIN_ENDPOINT"] = env_endpoint

    _tracing_enabled = True
    _tracing_initialized = True
    logger.info(f"[Tracing] LangSmith 追踪已启用 (project={env_project})")
    return True


def is_tracing_enabled() -> bool:
    """检查 LangSmith 追踪是否已启用"""
    global _tracing_enabled, _tracing_initialized
    if not _tracing_initialized:
        init_tracing()
    return _tracing_enabled


# ============================================================================
# LangChain Callback Handler
# ============================================================================

def get_langchain_callbacks():
    """
    获取 LangChain 回调处理器列表。

    在调用 LangChain 的 invoke/stream 时传入，
    自动将链路数据上报到 LangSmith。

    用法:
        from core.tracing import get_langchain_callbacks
        callbacks = get_langchain_callbacks()
        response = llm.invoke(prompt, config={"callbacks": callbacks})
    """
    if not is_tracing_enabled():
        return None

    try:
        from langchain_core.tracers import LangChainTracer
        tracer = LangChainTracer()
        return [tracer]
    except ImportError:
        logger.warning("[Tracing] langchain-core 未安装，无法创建 tracer")
        return None


# ============================================================================
# 自定义追踪上下文管理器
# ============================================================================

@contextmanager
def trace_run(name: str, metadata: Optional[Dict[str, Any]] = None):
    """
    追踪一个操作执行的上下文管理器。

    记录操作的开始、结束时间和状态。

    用法:
        with trace_run("schema_retrieval", {"query": question}):
            results = retriever.retrieve(question)
    """
    if not is_tracing_enabled():
        yield
        return

    start = time.time()
    run_id = f"{name}_{int(start * 1000)}"
    logger.info(f"[Trace] ⏳ {name} 开始 | run_id={run_id}")

    try:
        yield
        elapsed = time.time() - start
        logger.info(f"[Trace] ✓ {name} 完成 ({elapsed:.3f}s)")
    except Exception as e:
        elapsed = time.time() - start
        logger.error(f"[Trace] ✗ {name} 失败 ({elapsed:.3f}s): {e}")
        raise


# ============================================================================
# 追踪装饰器
# ============================================================================

def trace_operation(name: Optional[str] = None):
    """
    函数级追踪装饰器。

    自动记录函数的执行时间、参数和返回值（截断）。

    用法:
        @trace_operation("schema_retrieval")
        def retrieve_schema(question: str) -> List[Dict]:
            ...
    """
    def decorator(func: Callable) -> Callable:
        op_name = name or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            if not is_tracing_enabled():
                return func(*args, **kwargs)

            start = time.time()
            # 截断参数日志
            args_preview = [str(a)[:100] for a in args]
            kwargs_preview = {k: str(v)[:100] for k, v in kwargs.items()}

            logger.info(f"[Trace] ▶ {op_name} 调用 | args={args_preview}")

            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start
                result_preview = str(result)[:200] if result else "None"
                logger.info(
                    f"[Trace] ◀ {op_name} 返回 ({elapsed:.3f}s) | "
                    f"result={result_preview}"
                )
                return result
            except Exception as e:
                elapsed = time.time() - start
                logger.error(f"[Trace] ✗ {op_name} 异常 ({elapsed:.3f}s): {e}")
                raise

        return wrapper
    return decorator


# ============================================================================
# 操作记录器（用于将所有追踪输出整理为结构化日志）
# ============================================================================

class TraceRecorder:
    """
    追踪记录器 — 收集一次完整 Agent 调用的所有追踪信息。

    用于在结果中返回追踪明细，或在 Dashboard 中展示。
    """

    def __init__(self):
        self.spans: list = []
        self._current_span: Optional[dict] = None
        self._start_time: Optional[float] = None

    def start_run(self, question: str):
        """开始一次完整的 Agent 调用追踪"""
        self._start_time = time.time()
        self.spans = []
        logger.info(f"[TraceRecorder] ═══ 新查询追踪: '{question[:60]}...' ═══")

    def end_run(self) -> Dict:
        """结束追踪，返回汇总信息"""
        if self._start_time is None:
            return {}

        total_time = time.time() - self._start_time
        summary = {
            "total_time": round(total_time, 3),
            "spans": self.spans,
            "span_count": len(self.spans),
        }
        logger.info(f"[TraceRecorder] 追踪汇总: {len(self.spans)} 个操作, {total_time:.3f}s")
        return summary

    def record(self, operation: str, status: str, details: Dict, elapsed: float):
        """
        记录一个追踪事件。

        参数:
            operation: 操作名称（如 "cache_check", "llm_generate"）
            status: 状态（"start", "success", "failure", "hit", "miss"）
            details: 附加详情
            elapsed: 耗时（秒）
        """
        span = {
            "operation": operation,
            "status": status,
            "details": details,
            "elapsed": round(elapsed, 3),
            "timestamp": time.strftime("%H:%M:%S"),
        }
        self.spans.append(span)
        logger.info(f"[TraceRecorder] [{operation}] {status} ({elapsed:.3f}s)")


# ============================================================================
# 全局追踪记录器实例
# ============================================================================

_recorder: Optional[TraceRecorder] = None


def get_recorder() -> TraceRecorder:
    """获取全局追踪记录器"""
    global _recorder
    if _recorder is None:
        _recorder = TraceRecorder()
    return _recorder


def reset_recorder():
    """重置追踪记录器"""
    global _recorder
    _recorder = TraceRecorder()


# ============================================================================
# 独立测试入口
# ============================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # 测试追踪初始化（如果没有 API Key 会降级）
    enabled = init_tracing()
    print(f"Tracing enabled: {enabled}")

    # 测试上下文管理器
    with trace_run("test_operation", {"test": True}):
        time.sleep(0.1)
        print("  操作执行中...")

    # 测试装饰器
    @trace_operation("test_function")
    def dummy_function(x: int) -> str:
        time.sleep(0.05)
        return f"result={x}"

    dummy_function(42)

    # 测试 TraceRecorder
    recorder = TraceRecorder()
    recorder.start_run("测试查询")
    recorder.record("cache_check", "miss", {"query": "test"}, 0.001)
    recorder.record("llm_generate", "success", {"tokens": 150}, 2.5)
    summary = recorder.end_run()
    print(f"追踪汇总: {summary}")
