"""
==============================================================================
Critic Agent — SQL 校验 + Self-Correction + Trace 检索
==============================================================================
设计思路：
  1. 对 Generator 生成的 SQL 做语法校验（sqlglot）
  2. SQL 执行失败时触发 Self-Correction 闭环
     - 检索 traces/ 中相似错误历史
     - 构建增强修正 Prompt → 调用 Generator 修正 → 重试
     - 最多 2 次，失败记录 Trace
  3. Trace 检索：字段名错误时，优先从错题本找历史修正方案
==============================================================================
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core.config import CONFIG
from core.llm_client import BaseLLMClient

logger = logging.getLogger("critic_agent")

MAX_RETRY_LIMIT = 2
TRACES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "traces",
)

SQL_CORRECTION_PROMPT = """你是一名 SQL 修复专家。用户提问："{original_query}"。
你之前生成了以下 SQL，但执行失败。

SQL：{failed_sql}
错误信息：{error_message}
相关表结构：{schema_context}

{trace_hint}

【强制】表名只能从上方【相关表结构】中列出的可用表中选择，禁止使用任何不在列表中的表名。

请分析错误原因，并生成一个修正后的、仅包含 SELECT 的 SQL 语句。
只输出 SQL，不要解释。
"""

SQL_CRITIC_SYSTEM_PROMPT = """你是 SQL 数据库专家。检查以下 SQL 语句是否存在问题。

检查要点：
1. 语法是否正确
2. 函数使用是否正确
3. 是否安全（只允许 SELECT）
4. 字段名和表名是否真实存在

如果没有问题，返回：
{"valid": true, "issues": [], "suggestion": ""}

如果发现问题，返回：
{"valid": false, "issues": ["问题1", "问题2"], "suggestion": "建议修复方案"}

只输出 JSON。"""


def record_trace(
    original_query: str,
    failed_sql: str,
    error_message: str,
    retry_count: int,
    final_result: str,
):
    """记录 Self-Correction 过程到 Trace 文件"""
    try:
        os.makedirs(TRACES_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        desc = re.sub(r"[^\w一-鿿]", "_", original_query[:30])
        filename = f"{timestamp}_{desc}.md"
        filepath = os.path.join(TRACES_DIR, filename)

        content = f"""# Trace: SQL 自我修正 — "{original_query[:80]}"

**记录时间**：{datetime.now().strftime("%Y-%m-%d %H:%M")}
**发现人**：AI（Self-Correction SOP）

## 触发场景
- 用户原始输入：{original_query}
- 执行失败的 SQL：{failed_sql}
- 错误信息：{error_message}

## 修正过程
- 重试次数：{retry_count}/{MAX_RETRY_LIMIT}
- 最终结果：{"成功" if final_result != "失败" else "失败"}

## 最终 SQL
```sql
{final_result}
```

## 相关 Trace 链接
- 关联 SOP：.harness/skills/self-correction-sop.md
"""
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"[Trace] 已记录修正过程: {filepath}")
    except Exception as e:
        logger.warning(f"[Trace] 记录失败: {e}")


class CriticAgent:
    """
    SQL 校验 + 自我修正 + Trace 检索。

    用法:
        critic = CriticAgent(llm_critic, llm_generator)
        is_valid, issues = critic.validate_syntax(sql)
        fixed_sql = critic.self_correction_loop(query, sql, error, schema)
    """

    def __init__(
        self,
        llm_critic: BaseLLMClient,
        llm_generator: BaseLLMClient,
    ):
        self.llm_critic = llm_critic
        self.llm_generator = llm_generator
        self.max_retries = MAX_RETRY_LIMIT
        self.traces_dir = TRACES_DIR

        self._enhanced_validator = None
        try:
            from services.sql_validator import SQLValidator as EnhancedSQLValidator
            self._enhanced_validator = EnhancedSQLValidator(dialect="mysql")
            logger.info("[CriticAgent] sqlglot 增强验证器已加载")
        except Exception as e:
            logger.info(f"[CriticAgent] sqlglot 增强验证器未加载: {e}")

        logger.info("[CriticAgent] 初始化完成")

    # ========================================================================
    # Trace 检索
    # ========================================================================

    def search_similar_trace(
        self, error_message: str, query: str
    ) -> Optional[str]:
        """
        从 traces/ 检索与当前字段错误相似的 Trace。

        当错误为 "Unknown column 'xxx'" 时，
        查找历史 Trace 中修正过相同字段的记录。

        返回:
            格式化的历史修正提示，未找到返回 None
        """
        if not os.path.isdir(self.traces_dir):
            return None

        error_lower = error_message.lower()
        if "unknown column" not in error_lower and "字段" not in error_lower:
            return None

        try:
            trace_files = sorted(
                [f for f in os.listdir(self.traces_dir) if f.endswith(".md")],
                reverse=True,
            )[:10]
        except Exception:
            return None

        # 提取错误中的字段名
        field_match = re.search(
            r"unknown\s+column\s+['`]\s*(\w+)\s*['`]", error_lower
        )
        error_field = field_match.group(1) if field_match else None
        if not error_field:
            return None

        for tf in trace_files:
            try:
                path = os.path.join(self.traces_dir, tf)
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read()
                if error_field in content.lower():
                    sql_match = re.search(
                        r"最终 SQL\s*\n```sql\n(.*?)```",
                        content,
                        re.DOTALL,
                    )
                    if sql_match:
                        logger.info(
                            f"[CriticAgent] 从 Trace '{tf}' 找到相似修正"
                        )
                        return (
                            f"【历史相似修正（来自 {tf}）】\n"
                            f"当时修正的 SQL：{sql_match.group(1).strip()[:300]}"
                        )
            except Exception:
                continue

        return None

    # ========================================================================
    # SQL 校验
    # ========================================================================

    def validate_syntax(
        self, sql: str, schema_context: str = ""
    ) -> Tuple[bool, List[str]]:
        """sqlglot + LLM 双层 SQL 校验"""
        issues = []
        if not sql or not sql.strip():
            return False, ["SQL 为空"]

        if self._enhanced_validator is not None:
            try:
                is_valid, v_errors = self._enhanced_validator.validate_all(sql)
                if v_errors:
                    for verr in v_errors:
                        issues.append(f"[{verr.error_type}] {verr.message}")
            except Exception as e:
                logger.debug(f"[CriticAgent] sqlglot 校验异常: {e}")
        else:
            sql_upper = sql.strip().upper()
            if not re.match(r"^\s*(SELECT|WITH)\b", sql_upper):
                issues.append("SQL 不是以 SELECT/WITH 开头")

        try:
            prompt = f"检查以下 SQL 语句：\n```sql\n{sql}\n```\n"
            if schema_context:
                prompt += f"\n表结构：\n{schema_context[:1000]}"
            result = self.llm_critic.generate_json(
                prompt=prompt,
                system_prompt=SQL_CRITIC_SYSTEM_PROMPT,
            )
            if result and not result.get("valid", True):
                issues.extend(result.get("issues", []))
        except Exception as e:
            logger.debug(f"[CriticAgent] LLM 校验异常: {e}")

        return len(issues) == 0, issues

    # ========================================================================
    # Self-Correction 闭环
    # ========================================================================

    def self_correction_loop(
        self,
        original_query: str,
        failed_sql: str,
        error_message: str,
        schema_context: str,
    ) -> Optional[str]:
        """
        Self-Correction 闭环。

        流程：
        1. 检索 Trace 中相似错误的历史修正
        2. 构建增强 Prompt（含历史修正提示）
        3. 调用 Generator 生成修正 SQL
        4. 重试最多 MAX_RETRY_LIMIT 次
        """
        current_sql = failed_sql
        current_error = error_message
        retry_count = 0

        logger.info(f"[CriticAgent] 启动 Self-Correction")

        # ---- 检索相似 Trace ----
        trace_hint = self.search_similar_trace(error_message, original_query)
        if trace_hint:
            logger.info(f"[CriticAgent] 找到历史 Trace，注入修正 Prompt")

        while retry_count < self.max_retries:
            retry_count += 1
            logger.info(
                f"[CriticAgent] 第 {retry_count}/{self.max_retries} 次修正..."
            )

            correction_prompt = SQL_CORRECTION_PROMPT.format(
                original_query=original_query,
                failed_sql=current_sql,
                error_message=current_error,
                schema_context=schema_context or "（无表结构）",
                trace_hint=trace_hint or "",
            )

            try:
                corrected_sql = self.llm_generator.generate(
                    prompt=correction_prompt,
                    system_prompt=(
                        "你是一名 SQL 修复专家。只输出修正后的 SQL，"
                        "用 ```sql`` 包裹，不要输出解释文字。"
                    ),
                )

                if not corrected_sql:
                    current_error = "修正返回空 SQL"
                    continue

                # 提取 SQL
                from agents.generator_agent import fix_sql_quoting as _fix_quoting

                sql_match = re.search(
                    r"```sql\s*\n?(.*?)\n?```",
                    corrected_sql,
                    re.DOTALL | re.IGNORECASE,
                )
                if sql_match:
                    corrected_sql = sql_match.group(1).strip()
                else:
                    lines = corrected_sql.strip().split("\n")
                    sql_lines = []
                    for line in lines:
                        if line.strip().upper().startswith(("SELECT", "WITH")):
                            sql_lines.append(line)
                        elif sql_lines and line.strip():
                            sql_lines.append(line)
                    if sql_lines:
                        corrected_sql = "\n".join(sql_lines)

                corrected_sql = _fix_quoting(corrected_sql)

                logger.info(
                    f"[CriticAgent] 第 {retry_count} 次修正: {corrected_sql[:200]}"
                )

                is_valid, issues = self.validate_syntax(
                    corrected_sql, schema_context
                )
                if not is_valid:
                    current_error = f"校验不通过: {'; '.join(issues)}"
                    current_sql = corrected_sql
                    continue

                record_trace(
                    original_query=original_query,
                    failed_sql=failed_sql,
                    error_message=error_message,
                    retry_count=retry_count,
                    final_result=corrected_sql,
                )
                return corrected_sql

            except Exception as e:
                current_error = str(e)
                logger.error(f"[CriticAgent] 第 {retry_count} 次修正异常: {e}")

        logger.warning(f"[CriticAgent] 自我修正失败 ({self.max_retries} 次后放弃)")
        record_trace(
            original_query=original_query,
            failed_sql=failed_sql,
            error_message=error_message,
            retry_count=retry_count,
            final_result="失败",
        )
        return None
