"""
==============================================================================
Generator Agent — SQL 语句生成（零静态映射）
==============================================================================
设计思路：
  不再依赖 SYNONYM_MAP 等硬编码映射。
  Prompt 中只包含 Schema Retriever 提供的真实表结构（含注释和样本值），
  LLM 直接从 Schema 中推断字段映射关系。

  fix_sql_quoting 保留（纯语法修复，与映射无关）。
==============================================================================
"""

import json
import logging
import re
from typing import Optional, Tuple

from core.llm_client import BaseLLMClient

logger = logging.getLogger("generator_agent")

# ============================================================================
# Prompt 模板 — 不包含任何硬编码映射
# ============================================================================

SQL_GENERATION_SYSTEM_PROMPT = """你是专业的 SQL 生成专家。根据用户问题和数据库 Schema 生成 SQL 语句。

## 核心约束（必须严格遵守）
1. 只生成 SELECT 查询，禁止 INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE
2. 除非用户明确要求全量数据，否则必须添加 LIMIT 50
3. 【表名】必须严格与下方 Schema 中列出的表名完全一致，禁止任何简写、缩写或别名
4. 【字段名】只能使用下方 Schema 中真实存在的列名，严禁编造
5. 【字段值】WHERE 条件中的文本值必须从字段的样本值（e.g.）中选择
6. 表名使用反引号 ``，字符串值使用单引号 ''

## 输出格式
先写一段中文思考过程（50-150字），分析用户问题对应的表和字段，然后另起一行用 ```sql``` 包裹最终 SQL。

## 思考过程示例
分析用户问题“`各省销售额`”，需要统计每个省份的销售总额，涉及 `orders` 表的 `province` 和 `total_amount` 字段，需要按 province 分组求和。

```sql
SELECT province, SUM(total_amount) as total_sales FROM `orders` GROUP BY province ORDER BY total_sales DESC
```"""

SQL_GENERATION_PROMPT_TEMPLATE = """
【用户问题】
{user_question}

【数据库表结构（含注释和样本值）】
{schema_str}
{trace_hints}
{preference_hints}
## 强制规则
- 表名必须严格使用上方 Schema 中列出的真实表名
- 字段名只能使用 Schema 中真实存在的列名
- 先输出 50-150 字中文思考过程分析问题对应的表和字段，再输出 SQL
"""


# ============================================================================
# SQL 后处理 — 修复 LLM 常见的标识符引号错误
# ============================================================================

def fix_sql_quoting(sql: str) -> str:
    """修复 LLM 生成 SQL 中常见的引号错误"""
    if not sql:
        return sql
    sql = re.sub(r"(?i)(\bJOIN\s+)'(\w+)'\s+`(\w+)`", r"\1`\2` \3", sql)
    sql = re.sub(r"(?i)(\bJOIN\s+)'(\w+)'\s+(\w+)", r"\1`\2` \3", sql)
    sql = re.sub(r"'(\w+)'\s*\.\s*`?(\w+)`?", r"`\1`.\2", sql)
    sql = re.sub(r"(?<!\w)'(\w+)'(?!\w)", r"`\1`", sql)
    sql = re.sub(
        r"(?i)(=|>|<|>=|<=|!=|IN)\s*`([^`]+)`\s*",
        lambda m: (
            f"{m.group(1)} '{m.group(2)}' "
            if not m.group(2).isdigit()
            else m.group(0)
        ),
        sql,
    )
    sql = re.sub(
        r"(?i)"
        r"(\b(?:FROM|JOIN|ON|GROUP\s+BY|ORDER\s+BY|INTO|TABLE)\s+"
        r"[^'`\s]*?)'(\w+)'(?!\s*=)",
        lambda m: m.group(0).replace(f"'{m.group(2)}'", f"`{m.group(2)}`", 1),
        sql,
    )
    return sql


# ============================================================================
# Generator Agent
# ============================================================================

class GeneratorAgent:
    """
    SQL 生成 Agent。

    不再依赖任何 SYNONYM_MAP。
    所有字段映射信息来自 Schema Retriever 提供的真实表结构。
    """

    def __init__(self, llm_generator: BaseLLMClient):
        self.llm = llm_generator
        logger.info("[GeneratorAgent] 初始化完成（零静态映射）")

    def generate(
        self,
        query: str,
        schema_context: str,
        term_hints: str = "",
        field_values: str = "",
        resolved_fields: str = "",
        resolved_values: str = "",
        trace_hints: str = "",
        preference_hints: str = "",
    ) -> dict:
        """
        根据用户问题和 Schema 生成 SQL（支持记忆注入）。

        参数:
            query: 用户问题
            schema_context: Schema Retriever 提供的增强 Schema 文本（含注释+样本值）
            term_hints: 保留参数，不再使用
            field_values: 保留参数，不再使用
            resolved_fields: 已解析的字段映射提示（可选）
            resolved_values: 已解析的值映射提示（可选）
            trace_hints: 历史修正参考（从全局+对话级 Trace 检索，可选）
            preference_hints: 常用查询模式（从偏好积累，可选）

        返回:
            {"sql": str, "raw_response": str, "reasoning": str}
        """
        if not query:
            logger.warning("[GeneratorAgent] 空查询")
            return {"sql": "", "raw_response": "", "reasoning": ""}

        # 构造 Schema 文本（含增强信息 + 已解析的映射）
        enhanced_schema = schema_context or "（无表结构）"
        if resolved_fields:
            enhanced_schema += f"\n\n【已识别字段映射】\n{resolved_fields}"
        if resolved_values:
            enhanced_schema += f"\n\n【已识别值映射】\n{resolved_values}"

        # 格式化 Trace 和偏好区块（仅在非空时追加）
        trace_block = ""
        if trace_hints:
            trace_block = f"\n\n【历史修正参考】\n{trace_hints}"
        pref_block = ""
        if preference_hints:
            pref_block = f"\n\n【常用查询模式】\n{preference_hints}"

        prompt = SQL_GENERATION_PROMPT_TEMPLATE.format(
            user_question=query,
            schema_str=enhanced_schema,
            trace_hints=trace_block,
            preference_hints=pref_block,
        )

        logger.info(f"[GeneratorAgent] 生成 SQL (查询: '{query[:80]}...')")

        try:
            raw = self.llm.generate(
                prompt=prompt,
                system_prompt=SQL_GENERATION_SYSTEM_PROMPT,
            )
            sql = self.extract_sql(raw)
            if sql:
                sql = fix_sql_quoting(sql)
                logger.info(f"[GeneratorAgent] SQL:\n{sql}")

            # 提取思维链（SQL 之外的内容）
            reasoning = ""
            if raw and sql:
                idx = raw.find(sql)
                if idx > 0:
                    reasoning = raw[:idx].strip()
                elif sql in raw:
                    reasoning = raw.replace(sql, "").strip()
            if not reasoning:
                reasoning = raw.strip() if raw else ""

            return {"sql": sql, "raw_response": raw, "reasoning": reasoning}
        except Exception as e:
            logger.error(f"[GeneratorAgent] 生成失败: {e}")
            return {"sql": "", "raw_response": "", "reasoning": ""}

    def extract_sql(self, text: str) -> str:
        """从 LLM 输出中提取 SQL"""
        if not text:
            return ""

        text = text.strip()

        # ```sql ... ```
        m = re.search(r"```sql\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
        if not m:
            blocks = list(re.finditer(r"```\s*\n?(.*?)\n?```", text, re.DOTALL))
            if blocks:
                m = blocks[-1]

        if m:
            sql = m.group(1).strip()
            match = re.search(r"(SELECT|WITH)\s", sql, re.IGNORECASE)
            if match:
                sql = sql[match.start():]
            return sql

        # 【SQL】标记
        m = re.search(r"【SQL】\s*\n?(.*?)$", text, re.DOTALL)
        if m:
            c = m.group(1).strip()
            m2 = re.search(r"```(?:sql)?\s*\n?(.*?)\n?```", c, re.DOTALL)
            if m2:
                return m2.group(1).strip()
            if re.match(r"^(SELECT|WITH)\s", c, re.IGNORECASE):
                return c

        # "SQL:" 标记
        m = re.search(r"SQL:\s*(.*)", text, re.DOTALL)
        if m:
            return m.group(1).strip()

        # SELECT/WITH 开头的行
        lines = text.strip().split("\n")
        sql_lines = []
        for line in lines:
            line = line.strip()
            if line.upper().startswith(("SELECT", "WITH", "--")):
                sql_lines.append(line)
            elif sql_lines and line and not line.startswith(("【", "```", "{")):
                sql_lines.append(line)

        if sql_lines:
            result = "\n".join(sql_lines)
            result = re.sub(r"^-- .*\n?", "", result).strip()
            return result

        return ""
