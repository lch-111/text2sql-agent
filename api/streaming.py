"""
==============================================================================
SSE 流式聊天处理 — 流式生成 SQL + 执行结果
==============================================================================

设计思路：
  不修改 agent.py，而是创建一个并行的流式 LLM 客户端，
  复用 agent 的 _retrieve_schema、_extract_sql、FewShotManager 等方法。
==============================================================================
"""

import asyncio
import json
import logging
import time
from typing import AsyncGenerator, Optional

from config import CONFIG

logger = logging.getLogger("streaming")


def _sse(event: str, data: dict) -> str:
    """格式化 SSE 消息"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _create_streaming_llm():
    """创建流式 LLM 客户端（不修改 agent.py）"""
    cfg = CONFIG.llm
    if cfg.provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=cfg.openai_model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
            api_key=cfg.openai_api_key,
            base_url=cfg.openai_base_url or None,
            streaming=True,
        )
    elif cfg.provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            base_url=cfg.ollama_base_url,
            model=cfg.ollama_model,
            temperature=cfg.temperature,
            max_tokens=cfg.max_tokens,
        )
    raise ValueError(f"不支持的 LLM provider: {cfg.provider}")


async def stream_chat(question: str) -> AsyncGenerator[str, None]:
    """
    流式处理聊天请求，产出 SSE 事件。

    事件类型:
      - step:      处理步骤状态更新
      - token:     LLM 生成的文本片段
      - sql:       最终提取的 SQL
      - result:    查询结果
      - error:     错误信息
      - done:      流结束
    """
    from cache import get_cache
    from agent import (TextToSQLAgent, SQLValidator, SQL_FROM_INTENT_PROMPT,
                       SEMANTIC_PARSE_PROMPT, fix_sql_quoting)

    agent = TextToSQLAgent()
    start_time = time.time()

    # ---- Step 1: 查询预处理 + 缓存检查 ----
    yield _sse("step", {"step": "cache_check", "message": "检查缓存中..."})
    agent._load_distinct_values()
    normalized_q = agent.normalize_user_query(question)
    cache = get_cache()
    cached = cache.get(normalized_q)
    if cached:
        result_data = cached.get("result", [])
        columns = list(result_data[0].keys()) if result_data and isinstance(result_data[0], dict) else []
        yield _sse("result", {
            "sql": cached["sql"],
            "result": result_data,
            "columns": columns,
            "cache_hit": True,
            "cache_source": cached.get("source", "L1"),
            "execution_time": time.time() - start_time,
        })
        yield _sse("done", {})
        return

    # ---- Step 2: 语义解析 ----
    yield _sse("step", {"step": "semantic_parse", "message": "解析查询意图..."})
    parsed = agent._semantic_parse(normalized_q)

    if parsed.get("clarification_needed"):
        missing = parsed.get("missing_info", [])
        q = parsed.get("clarification_question", "") or f"请补充: {', '.join(missing)}"
        yield _sse("result", {
            "sql": None, "result": [], "columns": [],
            "clarification": q, "cache_hit": False,
            "execution_time": round(time.time() - start_time, 2),
        })
        yield _sse("done", {})
        return

    # ---- Step 3: 构建 Prompt（含语义解析 + Schema + 字段实际值）----
    yield _sse("step", {"step": "building_prompt", "message": "构建查询..."})
    schema_text = agent._build_schema_text()
    db_type = agent.db.active_db_type if hasattr(agent, 'db') else 'sqlite'
    limit = parsed.get("limit", 50)

    term_hints = agent._build_term_hints() if hasattr(agent, '_build_term_hints') else ""
    prompt = SQL_FROM_INTENT_PROMPT.format(
        db_type=db_type,
        schema_str=schema_text,
        term_hints=term_hints or "（无）",
        parsed_intent_json=json.dumps(parsed, ensure_ascii=False, indent=2),
        user_question=question,
        limit=limit,
    )

    # ---- Step 4: 流式调用 LLM ----
    yield _sse("step", {"step": "generating", "message": "AI 正在生成 SQL..."})
    llm = _create_streaming_llm()
    full_response = ""
    try:
        async for chunk in llm.astream(prompt):
            token = chunk.content if hasattr(chunk, 'content') else str(chunk)
            full_response += token
            yield _sse("token", {"text": token})
    except Exception as e:
        yield _sse("error", {"error": f"LLM 调用失败: {e}"})
        yield _sse("done", {})
        return

    # ---- Step 5: 提取 SQL ----
    sql = TextToSQLAgent._extract_sql(full_response)
    logger.info(f"[stream] extracted_sql={sql[:300]}")

    # 后处理：修正表名/列名单引号错误
    if not sql.startswith('{"clarification_needed"'):
        sql_fixed = fix_sql_quoting(sql)
        if sql_fixed != sql:
            sql = sql_fixed

    # 检测是否为澄清请求
    if sql.startswith('{"clarification_needed"'):
        try:
            clarification = json.loads(sql)
            yield _sse("result", {
                "sql": None,
                "result": [],
                "columns": [],
                "clarification": clarification.get("question", ""),
                "cache_hit": False,
                "cache_source": None,
                "execution_time": round(time.time() - start_time, 2),
            })
            yield _sse("done", {})
            return
        except json.JSONDecodeError:
            pass

    yield _sse("sql", {"sql": sql})

    # ---- Step 6: SQL 安全校验 ----
    yield _sse("step", {"step": "validating", "message": "校验 SQL 安全性..."})
    valid, err_msg = SQLValidator.validate(sql)
    if not valid:
        yield _sse("error", {"error": f"SQL 校验失败: {err_msg}"})
        yield _sse("done", {})
        return

    # ---- Step 7: 执行 SQL（只读事务 + 自动重试） ----
    yield _sse("step", {"step": "executing", "message": "执行 SQL 查询..."})

    # 执行前再强制修复一次引号（防止遗漏）
    if not sql.startswith('{"'):
        sql_fixed = fix_sql_quoting(sql)
        if sql_fixed != sql:
            logger.info(f"[stream] 执行前引号修复")
            sql = sql_fixed

    # 执行 + 最多 2 次重试
    last_error = None
    for exec_attempt in range(3):  # 首次 + 2 次重试
        try:
            from database import get_db
            db = get_db()
            df = await asyncio.get_event_loop().run_in_executor(
                None, db.query_readonly, sql
            )
            rows = df.to_dict(orient="records")
            columns = df.columns.tolist() if not df.empty else []

            # 写入缓存
            try:
                cache.set(normalized_q, sql, rows)
            except Exception as e:
                logger.warning(f"[stream] 缓存写入失败: {e}")

            elapsed = time.time() - start_time
            yield _sse("result", {
                "sql": sql,
                "result": rows,
                "columns": columns,
                "cache_hit": False,
                "cache_source": None,
                "execution_time": round(elapsed, 2),
            })
            yield _sse("done", {})
            return

        except Exception as e:
            last_error = str(e)
            if exec_attempt < 2:
                yield _sse("step", {
                    "step": "retrying",
                    "message": f"SQL 执行失败，正在重试 ({exec_attempt + 1}/2)...",
                })
                # 用错误信息修正 SQL
                try:
                    from agent import SQL_CORRECTION_PROMPT_TEMPLATE
                    correction_prompt = SQL_CORRECTION_PROMPT_TEMPLATE.format(
                        db_type=db_type,
                        schema_str=schema_for_prompt,
                        user_question=question,
                        wrong_sql=sql,
                        error_message=last_error,
                        recovery_strategy="请检查字段名是否正确，表关联是否完整，SQL 语法是否符合当前数据库标准。",
                    )
                    corrected = ""
                    async for chunk in llm.astream(correction_prompt):
                        token = chunk.content if hasattr(chunk, 'content') else str(chunk)
                        corrected += token
                    sql = TextToSQLAgent._extract_sql(corrected)
                    yield _sse("sql", {"sql": sql})
                    yield _sse("step", {"step": "retrying", "message": "已修正 SQL，重新执行..."})
                except Exception:
                    yield _sse("error", {"error": f"SQL 执行失败: {last_error}"})
                    yield _sse("done", {})
                    return

    # 所有重试耗尽
    yield _sse("error", {"error": f"SQL 执行失败，请换个问法或检查数据"})
    yield _sse("done", {})
