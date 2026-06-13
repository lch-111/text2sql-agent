"""
==============================================================================
FieldResolver — 字段消歧与映射提取（记忆优先、LLM 兜底）
==============================================================================
设计思路：
  将用户的自然语言字段引用映射为数据库实际字段/值。
  采用四级优先级链，尽量不消耗 Token：

    1. 全局 KV 缓存 — 跨对话已验证的映射（count >= 2）
    2. 对话级 KV 缓存 — 当前对话内已成功的映射
    3. 本地规则匹配 — 从 Schema 样本值做 fuzzy match
    4. LLM 消歧 — 前三级均未命中时调用 LLM 做二选一

  隐私设计：
    - 不存储原始用户问题
    - 存储的映射为抽象 {field, display_value, db_value}
==============================================================================
"""

import json
import logging
import re
from typing import Dict, List, Optional, Tuple

from core.llm_client import BaseLLMClient
from core.memory_manager import MemoryManager

logger = logging.getLogger("field_resolver")


class FieldResolver:
    """
    字段消歧器 — 四级优先级链解析字段映射。

    用法:
        resolver = FieldResolver(memory_manager, llm_client)
        result = resolver.resolve("查询广东省的销售额", schema_context, conv_id="xxx")
        # → {"resolved_fields": "...", "resolved_values": "..."}

        mappings = resolver.extract_mappings("查询广东省的销售额",
                                             "SELECT ... WHERE province = '广东'",
                                             conv_id="xxx")
        # → [{"field": "province", "display_value": "广东省", "db_value": "广东"}, ...]
    """

    def __init__(
        self,
        memory_manager: Optional[MemoryManager] = None,
        llm_client: Optional[BaseLLMClient] = None,
    ):
        """
        参数:
            memory_manager: MemoryManager 实例（用于全局/对话级记忆查询）
            llm_client: LLM 客户端（仅在前三级均未命中时使用）
        """
        self.memory = memory_manager
        self.llm = llm_client
        logger.info("[FieldResolver] 初始化完成（四级消歧链：全局KV→对话KV→本地规则→LLM）")

    # ========================================================================
    # 主入口 — 消歧
    # ========================================================================

    def resolve(
        self,
        query: str,
        schema_context: str = "",
        conv_id: Optional[str] = None,
    ) -> Dict[str, str]:
        """
        对用户问题执行字段消歧，返回可注入 Generator Prompt 的文本。

        参数:
            query: 用户原始问题（如 "查询广东省的销售额"）
            schema_context: Schema Retriever 提供的表结构文本
            conv_id: 当前对话 ID（可选）

        返回:
            {
                "resolved_fields": "【已识别字段映射】...",
                "resolved_values": "【已识别值映射】...",
            }
        """
        resolved_fields = []
        resolved_values = []

        # 1. 从 query 中提取潜在的字段/值引用
        potential_refs = self._extract_potential_refs(query, schema_context)

        for ref in potential_refs:
            field = ref.get("field", "")
            display_value = ref.get("display_value", "")

            if not field or not display_value:
                continue

            # ---- 四级消歧链 ----
            db_value = None

            # 第1级: 全局 KV
            if self.memory:
                db_value = self.memory.get_global_mapping(field, display_value)

            # 第2级: 对话级 KV
            if db_value is None and self.memory and conv_id:
                db_value = self.memory.get_conv_mapping(conv_id, field, display_value)

            # 第3级: 本地规则 — 从 schema_context 中做 fuzzy match
            if db_value is None and schema_context:
                db_value = self._local_fuzzy_match(display_value, schema_context, field)

            # 第4级: LLM 消歧
            if db_value is None and self.llm:
                db_value = self._llm_disambiguate(field, display_value, schema_context)

            if db_value and db_value != display_value:
                resolved_values.append(
                    f"- WHERE {field} = '{display_value}' → 实际值为 '{db_value}'"
                )
                resolved_fields.append(
                    f"- 字段 {field}: 用户输入 '{display_value}' → 数据库值 '{db_value}'"
                )

        result = {
            "resolved_fields": "\n".join(resolved_fields) if resolved_fields else "",
            "resolved_values": "\n".join(resolved_values) if resolved_values else "",
        }
        return result

    # ========================================================================
    # 映射提取 — 从成功的 Q→SQL 对中提取映射
    # ========================================================================

    def extract_mappings(
        self,
        query: str,
        sql: str,
        conv_id: Optional[str] = None,
    ) -> List[Dict[str, str]]:
        """
        从一次成功的查询（Q→SQL 对）中提取字段/值映射。

        用于 save_context_node 中：
          执行成功后调用此方法提取映射，然后存入 MemoryManager。

        隐私设计：
          返回的映射只包含 {field, display_value, db_value}，
          不包含原始问题文本和完整 SQL。

        参数:
            query: 用户问题
            sql: 生成的 SQL
            conv_id: 当前对话 ID

        返回:
            [{"field": "province", "display_value": "广东省", "db_value": "广东"}, ...]
        """
        mappings = []

        if not query or not sql:
            return mappings

        # 从 SQL 的 WHERE 子句中提取条件
        where_patterns = self._extract_where_conditions(sql)
        if not where_patterns:
            return mappings

        for field, db_value in where_patterns:
            # 在用户问题中查找对应的"口语化"引用
            display_value = self._find_display_value(query, field, db_value)
            if display_value and display_value != db_value:
                mappings.append({
                    "field": field,
                    "display_value": display_value,
                    "db_value": db_value,
                })

        return mappings

    # ========================================================================
    # 内部工具
    # ========================================================================

    def _extract_potential_refs(
        self, query: str, schema_context: str
    ) -> List[Dict[str, str]]:
        """
        从用户问题中提取潜在的字段/值引用。

        策略：
          - 从 schema_context 中提取所有字段名
          - 检查用户问题中是否包含这些字段名的常见口语化变体
          - 查找 WHERE 条件值的口语化表达
        """
        refs = []

        if not query or not schema_context:
            return refs

        # 提取 schema 中的所有字段名
        field_names = re.findall(
            r"(?:^|\n)\s*[-*]\s*`?(\w+)`?\s*\([\w, ]+\)",
            schema_context,
        )
        # 也尝试另一种格式: column_name (type) — comment
        if not field_names:
            field_names = re.findall(
                r"`?(\w+)`?\s*\([^)]+\)(?:\s*[—\-–]\s*.+)?",
                schema_context,
            )

        # 对每个字段，检查用户问题中是否包含可能的口语化引用
        for field in field_names:
            # 跳过明显不是值的字段（如 id, count 等）
            if field.lower() in ("id", "count", "total", "sum", "avg", "row_num"):
                continue

            # 从 schema_context 中查找该字段的样本值（e.g. 部分）
            sample_values = self._extract_sample_values(schema_context, field)
            for sample in sample_values:
                # 检查用户问题中是否包含相似的口语化值
                # 如 广东省 → 广东, 已完成 → 已完成
                if sample and len(sample) >= 2 and sample in query:
                    # 这可能是样本值直接出现在用户问题中
                    refs.append({
                        "field": field,
                        "display_value": sample,
                    })

        return refs

    def _local_fuzzy_match(
        self, display_value: str, schema_context: str, field: str
    ) -> Optional[str]:
        """
        本地规则匹配：从 Schema 的样本值中对 display_value 做模糊匹配。

        规则：
          - 如果 display_value 以某个样本值为前缀，即匹配（如 "广东省" → "广东"）
          - 如果样本值以 display_value 为前缀，也匹配
          - 使用最长公共子串长度超过 min(len(a), len(b)) * 0.6
        """
        sample_values = self._extract_sample_values(schema_context, field)

        best_match = None
        best_score = 0.0

        for sample in sample_values:
            if not sample or len(sample) < 1:
                continue
            # 计算匹配得分
            score = self._fuzzy_score(display_value, sample)
            if score > best_score:
                best_score = score
                best_match = sample

        # 阈值 0.6
        if best_score >= 0.6:
            logger.info(
                f"[FieldResolver] 本地规则匹配: '{display_value}' → '{best_match}'"
                f" (score={best_score:.2f})"
            )
            return best_match
        return None

    def _llm_disambiguate(
        self,
        field: str,
        display_value: str,
        schema_context: str,
    ) -> Optional[str]:
        """
        LLM 消歧：当前三级均未命中时，调用 LLM 做二选一。

        将 schema 中该字段的样本值列表发给 LLM，
        要求选择与 display_value 最匹配的实际值。
        """
        if not self.llm or not field or not display_value:
            return None

        sample_values = self._extract_sample_values(schema_context, field)
        if not sample_values or len(sample_values) < 2:
            return None

        prompt = (
            f"用户问题中提到「{display_value}」，数据库字段 {field} 的可选值为 "
            f"{json.dumps(sample_values, ensure_ascii=False)}。\n"
            f"请选择与「{display_value}」最匹配的实际数据库值，只输出值本身，不要其他内容。"
        )
        try:
            result = self.llm.generate(prompt=prompt, system_prompt="你是一名数据映射专家。")
            result = result.strip().strip("'\"")
            # 验证结果是否在可选值中
            if result in sample_values:
                logger.info(
                    f"[FieldResolver] LLM 消歧: '{display_value}' → '{result}'"
                )
                return result
        except Exception as e:
            logger.warning(f"[FieldResolver] LLM 消歧失败: {e}")

        return None

    @staticmethod
    def _extract_sample_values(schema_context: str, field: str) -> List[str]:
        """
        从 Schema 文本中提取指定字段的样本值。

        Schema 格式示例:
          - province(varchar) — 省份 e.g. 广东, 江苏, 浙江
        """
        values = []

        # 匹配行: `field` ... e.g. val1, val2, val3
        pattern = re.compile(
            rf"`?{re.escape(field)}`?\s*\([^)]*\)\s*[—\-–]\s*.+?e\.g\.[\s\.]*(.+)",
            re.IGNORECASE,
        )
        match = pattern.search(schema_context)
        if match:
            raw = match.group(1)
            # 分割逗号，清理引号和空格
            parts = re.split(r"[,，、]", raw)
            for p in parts:
                p = p.strip().strip("'\" ").strip()
                if p and len(p) >= 1:
                    values.append(p)

        return values

    @staticmethod
    def _fuzzy_score(a: str, b: str) -> float:
        """
        计算两个字符串的模糊匹配得分。

        使用最长公共子串长度 / max(len(a), len(b))
        """
        if not a or not b:
            return 0.0

        # 最长公共子串
        def lcs(s1, s2):
            m, n = len(s1), len(s2)
            dp = [[0] * (n + 1) for _ in range(m + 1)]
            max_len = 0
            for i in range(1, m + 1):
                for j in range(1, n + 1):
                    if s1[i - 1] == s2[j - 1]:
                        dp[i][j] = dp[i - 1][j - 1] + 1
                        max_len = max(max_len, dp[i][j])
            return max_len

        longest = lcs(a, b)
        return longest / max(len(a), len(b))

    @staticmethod
    def _extract_where_conditions(sql: str) -> List[Tuple[str, str]]:
        """
        从 SQL 的 WHERE 子句中提取 (字段, 值) 对。

        示例:
          WHERE province = '广东' → [("province", "广东")]
        """
        conditions = []

        # 查找 WHERE ... = '值' 的模式
        where_clause = re.search(
            r"WHERE\s+(.+?)(?:GROUP\s+BY|ORDER\s+BY|LIMIT|HAVING|$)",
            sql,
            re.IGNORECASE | re.DOTALL,
        )
        if not where_clause:
            return conditions

        where_text = where_clause.group(1)

        # 匹配 field = 'value' 或 field = "value"
        eq_patterns = re.findall(
            r"`?(\w+)`?\s*=\s*['\"](\w+)['\"]",
            where_text,
        )
        for field, value in eq_patterns:
            conditions.append((field, value))

        # 匹配 field IN ('v1', 'v2')
        in_patterns = re.findall(
            r"`?(\w+)`?\s+IN\s*\(([^)]+)\)",
            where_text,
            re.IGNORECASE,
        )
        for field, values_str in in_patterns:
            vals = re.findall(r"['\"](\w+)['\"]", values_str)
            for v in vals:
                conditions.append((field, v))

        return conditions

    @staticmethod
    def _find_display_value(query: str, field: str, db_value: str) -> Optional[str]:
        """
        在用户问题中查找与 db_value 对应的口语化表达。

        策略：
          - 如果 db_value 直接出现在 query 中，返回 db_value
          - 否则查找 query 中与 db_value 相似度最高且不同的词
        """
        if not query or not db_value:
            return None

        if db_value in query:
            return db_value

        # 从 query 中提取潜在的口语化表达
        # 通常是字段名后跟随的短语
        field_patterns = [
            rf"{re.escape(field)}\s*[是为：:]\s*(\S+)",
            rf"(\S+)\s*的\s*{re.escape(field)}",
        ]
        for pat in field_patterns:
            m = re.search(pat, query)
            if m:
                return m.group(1)

        return None
