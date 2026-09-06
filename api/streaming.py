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


# 最大等待时间（秒），超过此时间仍未完成视为超时
_STREAM_TIMEOUT = 120
# 心跳间隔（秒）
_PING_INTERVAL = 10


async def stream_chat(
    question: str, history: list = None, conv_id: str = "", thinking_mode: str = "normal"
) -> AsyncGenerator[str, None]:
    """
    SSE 流式聊天生成器。

    步骤：
      0. 提前验证关键导入（在 yield 之前完成，确保失败时不污染事件流）
      1. 缓存检查
      2. 分步通知事件
      3. 线程池执行 graph_execute（不阻塞事件循环）
         - 每 10s 发一次 ping 心跳
         - 最多等待 _STREAM_TIMEOUT 秒，超时报错
      4. 返回 sql/result/done 事件

    异常安全：
      - 所有 yield 均在 try 块内，异常时发送 error + done
      - asyncio.CancelledError 单独捕获（客户端断开）
    """
    start_time = time.time()
    sent_done = False  # 标记 done 事件是否已发送

    # ==========================================================================
    # Step 0: 提前验证关键导入 —— 在首次 yield 之前完成
    # ==========================================================================
    try:
        from core.graph import execute as graph_execute
        graph_execute  # 确认导入可用
    except Exception as e:
        logger.error(f"[streaming] 关键导入失败: {e}", exc_info=True)
        yield _sse("error", {"error": f"系统初始化失败: {str(e)[:200]}", "cid": conv_id})
        yield _sse("done", {"cid": conv_id})
        return

    loop = asyncio.get_event_loop()
    logger.info(f"[streaming] 开始流式输出 — conv_id={conv_id[:12] if conv_id else 'N/A'}, question={question[:60]}")

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
                    "cache_source": cached.get("source", "L1"),
                    "execution_time": round(time.time() - start_time, 2),
                    "thinking": "✅ 命中缓存，直接返回历史查询结果",
                }, conv_id))
                yield _sse("done", _with_cid({}, conv_id))
                sent_done = True
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

        # ---- Step 3: 在线程池中执行 graph_execute（不阻塞事件循环）----
        graph_future = loop.run_in_executor(
            None,
            lambda: graph_execute(question, conv_id=conv_id, thinking_mode=thinking_mode),
        )

        # 等待完成，内层每 10 秒发一次心跳，外层整体超时保护
        result = None  # 预先初始化，防止 future 在 while 前已完成导致 NameError
        max_pings = _STREAM_TIMEOUT // _PING_INTERVAL
        ping_count = 0

        while not graph_future.done():
            if ping_count >= max_pings:
                # 超过整体超时阈值，强行取消
                graph_future.cancel()
                logger.error(f"[streaming] 执行超时 ({_STREAM_TIMEOUT}s)，已取消")
                yield _sse("error", _with_cid({
                    "error": f"查询执行超时（超过 {_STREAM_TIMEOUT} 秒），请简化查询或稍后重试"
                }, conv_id))
                yield _sse("done", _with_cid({}, conv_id))
                sent_done = True
                return

            done_set, _ = await asyncio.wait([graph_future], timeout=_PING_INTERVAL)
            if graph_future.done():
                try:
                    result = graph_future.result()
                except Exception as exc:
                    # graph_execute 内部抛出的异常在此处 re-raise
                    raise exc
                break

            ping_count += 1
            yield _sse("ping", _with_cid({"ts": time.time()}, conv_id))

        # ---- Step 4: 处理结果 ----
        elapsed = round(time.time() - start_time, 2)

        # 无论结果如何，先告知前端已从后端拿到响应（确保前端一定有业务事件可消费）
        yield _sse("step", _with_cid({
            "step": "processing",
            "message": "查询完成，正在整理结果..."
        }, conv_id))

        # result 在 while 循环中赋值，但由于预先初始化，不会 NameError
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
                "cache_hit": result.get("cache_hit", False),
                "cache_source": result.get("cache_source"),
                "execution_time": elapsed,
                "conversation_history": result.get("conversation_history", []),
                "conv_id": conv_id,
                # 思维链与中间过程（前端用于展示 AI 分析步骤）
                "thinking": result.get("thinking", ""),
                "intent_reason": result.get("intent_reason", ""),
                "retries": result.get("retries", 0),
            }, conv_id))

        yield _sse("done", _with_cid({}, conv_id))
        sent_done = True

    except asyncio.CancelledError:
        logger.info("[streaming] 客户端断开连接")
        # CancelledError 下不可 yield，sent_done 保持 False
        # 但 finally 中也不 yield（generator 已终止），仅做日志

    except Exception as e:
        logger.error(f"[streaming] 失败: {e}", exc_info=True)
        try:
            yield _sse("error", _with_cid({"error": f"系统内部错误: {str(e)[:200]}"}, conv_id))
            yield _sse("done", _with_cid({}, conv_id))
            sent_done = True
        except GeneratorExit:
            pass

    finally:
        # 兜底：若 done 未被任何路径发送（极端异常），尝试补发
        if not sent_done:
            try:
                yield _sse("done", {"cid": conv_id})
            except GeneratorExit:
                pass
        logger.info(f"[streaming] 流结束 ({time.time()-start_time:.1f}s)")
