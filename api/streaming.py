"""
==============================================================================
SSE 流式聊天处理
==============================================================================
修复：coroutine was expected, got <Future pending...>
原因：asyncio.create_task() 需传入协程，但 run_in_executor() 返回 Future。
修复：直接使用 run_in_executor 返回的 Future。
==============================================================================
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator

logger = logging.getLogger("streaming")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _with_cid(data: dict, cid: str) -> dict:
    """为 SSE 事件数据绑定对话 ID，前端据此校验响应归属"""
    data["cid"] = cid
    return data


async def stream_chat(
    question: str, history: list = None, conv_id: str = "", thinking_mode: str = "normal"
) -> AsyncGenerator[str, None]:
    loop = asyncio.get_event_loop()
    start_time = time.time()

    try:
        # ---- Step 1: 缓存检查 ----
        yield _sse("step", _with_cid({"step": "cache_check", "message": "检查缓存中..."}, conv_id))
        await asyncio.sleep(0.03)

        try:
            from core.cache import get_cache
            cache = get_cache()
            cached = cache.get(question)
            if cached:
                rd = cached.get("result", [])
                cols = list(rd[0].keys()) if rd and isinstance(rd[0], dict) else []
                yield _sse("result", _with_cid({
                    "sql": cached["sql"],
                    "result": rd if rd else [],
                    "columns": cols or [],
                    "cache_hit": True,
                    "source": cached.get("source", "L1"),
                    "execution_time": round(time.time() - start_time, 2),
                }, conv_id))
                yield _sse("done", _with_cid({}, conv_id))
                return
        except Exception as e:
            logger.warning(f"[streaming] 缓存失败: {e}")

        # ---- Step 2: 分步通知 ----
        for step_name, msg in [
            ("routing", "分析查询意图..."),
            ("retrieving", "检索数据库结构..."),
            ("generating", "AI 正在生成 SQL..."),
            ("validating", "校验 SQL 安全性..."),
            ("executing", "执行 SQL 查询..."),
        ]:
            yield _sse("step", _with_cid({"step": step_name, "message": msg}, conv_id))
            await asyncio.sleep(0.03)

        # ---- Step 3: 执行 graph_execute（线程池，不阻塞）----
        from graph import execute as graph_execute

        # run_in_executor 返回 Future，不要用 create_task 包装（否则报 Future pending）
        graph_future = loop.run_in_executor(
            None,
            lambda: graph_execute(question, conv_id=conv_id, thinking_mode=thinking_mode),
        )

        # 每 10 秒检查一次完成状态，期间发心跳
        while not graph_future.done():
            done_set, _ = await asyncio.wait([graph_future], timeout=10)
            if graph_future.done():
                result = graph_future.result()
                break
            yield _sse("ping", _with_cid({"ts": time.time()}, conv_id))

        elapsed = round(time.time() - start_time, 2)

        if result is None:
            yield _sse("error", _with_cid({"error": "执行返回空结果"}, conv_id))
        elif result.get("error"):
            yield _sse("error", _with_cid({"error": result["error"]}, conv_id))
        else:
            yield _sse("sql", _with_cid({"sql": result.get("sql", "")}, conv_id))
            yield _sse("result", _with_cid({
                "sql": result.get("sql"),
                "result": result.get("result") if result.get("result") not in (None, []) else (result.get("result") or []),
                "columns": result.get("columns") or [],
                "cache_hit": False,
                "execution_time": elapsed,
                "conversation_history": result.get("conversation_history", []),
                "conv_id": conv_id,
            }, conv_id))

        yield _sse("done", _with_cid({}, conv_id))

    except asyncio.CancelledError:
        logger.info("[streaming] 客户端断开连接")

    except Exception as e:
        logger.error(f"[streaming] 失败: {e}", exc_info=True)
        try:
            yield _sse("error", _with_cid({"error": f"系统内部错误: {str(e)[:200]}"}, conv_id))
            yield _sse("done", _with_cid({}, conv_id))
        except GeneratorExit:
            pass

    finally:
        logger.info(f"[streaming] 流结束 ({time.time()-start_time:.1f}s)")
