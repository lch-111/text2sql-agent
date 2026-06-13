"""
==============================================================================
Schema Retriever — 零静态映射的全自动 Schema 检索 + 字段/值解析
==============================================================================
核心设计：
  不再依赖任何 term_mappings.json 或硬编码同义词。

  FieldResolver：
    resolve_field(term, table)  — 将用户口语字段名映射到数据库真实列名
    resolve_value(term)         — 将用户口语值映射到数据库实际存储值

  匹配策略（三段式，90% 情况零 Token 消耗）：
    1. KV 缓存 → 直接返回
    2. 本地规则（字段名/注释/样本值/编辑距离）
    3. 歧义时 GLM-4-Flash 消歧 → 写入缓存

  Schema 文本：
    每字段输出：列名(类型)  — 注释  e.g. val1, val2
==============================================================================
"""

import json
import logging
import os
import re
import time
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

import pandas as pd

from core.config import CONFIG
from core.database import get_db
from core.llm_client import BaseLLMClient

logger = logging.getLogger("schema_retriever")

# ============================================================================
# KV 缓存（内存级，复用 Redis 可选）
# ============================================================================
_field_cache: Dict[str, str] = {}     # "field:table:term" → "column_name"
_value_cache: Dict[str, str] = {}     # "value:table.col:term" → "actual_value"
_field_values_cache: Optional[Dict[str, List[str]]] = None
_field_values_loaded_at: Optional[float] = None
_FIELD_VALUES_TTL = 300


def _cache_key(prefix: str, *parts: str) -> str:
    return f"{prefix}:" + ":".join(parts)


# ============================================================================
# GLM 消歧 Prompt
# ============================================================================

_FIELD_DISAMBIGUATE_PROMPT = """你是数据库字段匹配专家。用户口语中提到一个字段，请从候选字段中选出最匹配的一个。

用户口语：{user_term}
表名：{table}
候选字段（名称 + 注释 + 样本值）：
{candidates}

输出 JSON：
{{"field": "最匹配的字段名", "confidence": 0.95, "reason": "简短理由"}}

规则：
1. 优先匹配字段名（如"金额"→"amount"）
2. 其次匹配注释关键词
3. 再匹配样本值
4. 完全无法匹配时 field 设为 null，confidence 0"""

_VALUE_DISAMBIGUATE_PROMPT = """你是数据库值匹配专家。用户口语中提到一个值，请从候选实际值中选出最匹配的一个。

用户口语：{user_term}
字段：{field}
候选实际值：{candidates}

输出 JSON：
{{"value": "最匹配的实际值", "confidence": 0.95, "reason": "简短理由"}}

规则：
1. 优先精确匹配（如"广东"→"广东"）
2. 其次编辑距离匹配（如"广东省"→"广东"）
3. 再尝试近义词匹配
4. 完全无法匹配时 value 设为 null，confidence 0"""


# ============================================================================
# FieldResolver
# ============================================================================

class FieldResolver:
    """
    字段 + 值 全自动解析器。

    不依赖任何静态映射文件，完全基于数据库真实 Schema 和样本值。
    规则匹配零 Token 消耗，仅歧义时调 GLM。
    """

    def __init__(
        self,
        schema_retriever: "SchemaRetriever",
        llm_client: Optional[BaseLLMClient] = None,
    ):
        self.sr = schema_retriever
        self.llm = llm_client
        self._rule_threshold = 0.6

    # ========================================================================
    # 字段名解析  resolve_field(user_term, table) → column_name
    # ========================================================================

    def resolve_field(self, user_term: str, table: str) -> Optional[str]:
        """将用户口语字段名映射到数据库真实列名"""
        user_term = user_term.strip()
        if not user_term:
            return None

        key = _cache_key("field", table, user_term)

        # 1. KV 缓存
        cached = _field_cache.get(key)
        if cached:
            return cached

        # 2. 规则匹配
        field, score = self._rule_match_field(user_term, table)
        if field and score >= self._rule_threshold:
            _field_cache[key] = field
            return field

        # 3. GLM 消歧
        if self.llm and field:
            llm_field = self._llm_disambiguate_field(user_term, table)
            if llm_field:
                _field_cache[key] = llm_field
                return llm_field

        return field  # 即使分低也返回最佳结果

    def _rule_match_field(self, term: str, table: str) -> Tuple[Optional[str], float]:
        """三段规则匹配字段名：名称 → 注释 → 样本值"""
        schema = self.sr.load_schema()
        tbl = schema.get(table)
        if not tbl:
            return None, 0.0

        term_low = term.lower()
        best_field, best_score = None, 0.0

        for col in tbl["columns"]:
            name = col["name"]
            comment = (col.get("comment") or "").lower()
            score = 0.0

            # ① 字段名匹配
            nl = name.lower()
            if term_low == nl:
                score = 1.0
            elif term_low in nl or nl in term_low:
                score = 0.85
            else:
                sim = SequenceMatcher(None, term_low, nl).ratio()
                if sim > 0.5:
                    score = sim * 0.8

            # ② 注释匹配
            if score < 0.8 and comment:
                tw = set(w for w in re.split(r"[\s/（(]", term_low) if len(w) >= 2)
                cw = set(w for w in re.split(r"[\s/（(,，]", comment) if len(w) >= 2)
                if tw & cw:
                    score = max(score, len(tw & cw) / max(len(tw), 1) * 0.7)

            # ③ 样本值匹配
            if score < 0.7:
                for val in self.sr.load_field_values().get(f"{table}.{name}", []):
                    vl = val.lower()
                    if term_low == vl:
                        score = max(score, 0.6)
                    elif len(term_low) >= 2 and len(vl) >= 2 and (term_low in vl or vl in term_low):
                        score = max(score, 0.5)

            if score > best_score:
                best_score, best_field = score, name

        return best_field, best_score

    def _llm_disambiguate_field(self, term: str, table: str) -> Optional[str]:
        """GLM 消歧字段名"""
        schema = self.sr.load_schema()
        tbl = schema.get(table)
        if not tbl or not self.llm:
            return None

        scored = []
        for col in tbl["columns"]:
            _, s = self._rule_match_field(term, table)
            scored.append((s, col))
        scored.sort(key=lambda x: x[0], reverse=True)

        cand = "\n".join(
            f"  {i+1}. {c['name']} | 注释: {c.get('comment','')} | "
            f"样本: {self._samples(table, c['name'])}"
            for i, (_, c) in enumerate(scored[:5])
        )

        try:
            r = self.llm.generate_json(
                _FIELD_DISAMBIGUATE_PROMPT.format(
                    user_term=term, table=table, candidates=cand
                )
            )
            return r["field"] if r and r.get("field") else None
        except Exception:
            return None

    # ========================================================================
    # 值解析  resolve_value(term, field_table_col) → actual_db_value
    # ========================================================================

    def resolve_value(self, term: str, field_key: str = "") -> Optional[str]:
        """
        将用户口语值映射到数据库实际存储值。

        "广东省" → "广东"（如果字段 province 的实际值列表中有"广东"）

        参数:
            term: 用户口语值（如"广东省"、"北京市"）
            field_key: 可选的字段键（如"orders.province"），缩小搜索范围

        返回:
            数据库中的实际值，未找到返回 None
        """
        term = term.strip()
        if not term:
            return None

        key = _cache_key("val", field_key or "*", term)

        # 1. KV 缓存
        cached = _value_cache.get(key)
        if cached:
            return cached

        # 2. 规则匹配
        val, source = self._rule_match_value(term, field_key)
        if val:
            _value_cache[key] = val
            return val

        # 3. GLM 消歧（仅在 field_key 明确时）
        if self.llm and field_key:
            llm_val = self._llm_disambiguate_value(term, field_key)
            if llm_val:
                _value_cache[key] = llm_val
                return llm_val

        return val

    def _rule_match_value(
        self, term: str, field_key: str = ""
    ) -> Tuple[Optional[str], str]:
        """
        值规则匹配：精确 → 编辑距离 → 子串。

        返回:
            (matched_value, source_type)
            source_type: "exact" | "fuzzy" | "substr"
        """
        vals = self.sr.load_field_values()
        term_low = term.lower().strip()

        if field_key and field_key in vals:
            candidates = [(field_key, vals[field_key])]
        else:
            candidates = list(vals.items())

        best_val, best_score, best_src = None, 0.0, ""

        for fk, fvals in candidates:
            for fv in fvals:
                fv_low = fv.lower().strip()
                # 精确
                if term_low == fv_low:
                    return fv, "exact"
                # 编辑距离
                sim = SequenceMatcher(None, term_low, fv_low).ratio()
                if sim > 0.8 and sim > best_score:
                    best_val, best_score, best_src = fv, sim, "fuzzy"
                # 子串
                if len(term_low) >= 2 and len(fv_low) >= 2:
                    if term_low in fv_low or fv_low in term_low:
                        sub_score = min(len(term_low), len(fv_low)) / max(len(term_low), len(fv_low), 1)
                        if sub_score > best_score:
                            best_val, best_score, best_src = fv, sub_score, "substr"

        return (best_val, best_src) if best_score >= 0.5 else (None, "")

    def _llm_disambiguate_value(self, term: str, field_key: str) -> Optional[str]:
        """GLM 消歧值"""
        vals = self.sr.load_field_values().get(field_key, [])
        if not vals or not self.llm:
            return None

        try:
            r = self.llm.generate_json(
                _VALUE_DISAMBIGUATE_PROMPT.format(
                    user_term=term,
                    field=field_key,
                    candidates=", ".join(vals[:10]),
                )
            )
            return r["value"] if r and r.get("value") else None
        except Exception:
            return None

    def _samples(self, table: str, field: str) -> str:
        vals = self.sr.load_field_values().get(f"{table}.{field}", [])
        return ", ".join(vals[:2]) if vals else "—"


# ============================================================================
# SchemaRetriever（对外入口）
# ============================================================================

class SchemaRetriever:
    """
    数据库 Schema 检索 + 字段/值解析。

    用法:
        sr = SchemaRetriever(reranker_client)
        sr.resolve_field("销售额", "orders")   # → "amount"
        sr.resolve_value("广东省", "orders.province")  # → "广东"
        sr.build_schema_text("广东销售额")      # → 增强 Schema 文本
    """

    _schema_cache: Optional[Dict] = None
    _schema_loaded_at: Optional[float] = None
    _SCHEMA_CACHE_TTL = 300

    def __init__(self, reranker_client: Optional[BaseLLMClient] = None):
        self.reranker = reranker_client
        self.db = get_db()
        self._top_k_search = 10
        self._top_k_rerank = 3

        # FieldResolver（字段 + 值 统一解析）
        self.resolver = FieldResolver(
            schema_retriever=self,
            llm_client=reranker_client,
        )

        logger.info("[SchemaRetriever] 初始化完成（零静态映射）")

    # ========================================================================
    # 对外接口
    # ========================================================================

    def resolve_field(self, term: str, table: str) -> Optional[str]:
        """将用户口语字段名解析为数据库列名"""
        return self.resolver.resolve_field(term, table)

    def resolve_value(self, term: str, field_key: str = "") -> Optional[str]:
        """将用户口语值解析为数据库实际值"""
        return self.resolver.resolve_value(term, field_key)

    # ========================================================================
    # Schema 加载
    # ========================================================================

    def load_schema(self) -> Dict:
        now = time.time()
        if (
            self._schema_cache is not None
            and self._schema_loaded_at
            and now - self._schema_loaded_at < self._SCHEMA_CACHE_TTL
        ):
            return self._schema_cache

        schema = {}
        try:
            from sqlalchemy import inspect as sa_inspect
            inspector = sa_inspect(self.db.engine)
            for tbl_name in inspector.get_table_names():
                cols = inspector.get_columns(tbl_name)
                pks = set(
                    inspector.get_pk_constraint(tbl_name).get("constrained_columns", [])
                )
                fks = inspector.get_foreign_keys(tbl_name)
                schema[tbl_name] = {
                    "columns": [
                        {
                            "name": c["name"],
                            "type": str(c["type"]),
                            "nullable": c.get("nullable", True),
                            "is_pk": c["name"] in pks,
                            "comment": c.get("comment", "") or "",
                        }
                        for c in cols
                    ],
                    "primary_key": list(pks),
                    "foreign_keys": [
                        {
                            "column": fk["constrained_columns"][0],
                            "ref_table": fk["referred_table"],
                            "ref_column": fk["referred_columns"][0]
                            if fk["referred_columns"] else "",
                        }
                        for fk in fks if fk["constrained_columns"]
                    ],
                }
            logger.info(f"[Schema] 已加载 {len(schema)} 个表")
        except Exception as e:
            logger.warning(f"[Schema] 加载失败: {e}")

        self._schema_cache = schema
        self._schema_loaded_at = time.time()
        return schema

    def load_field_values(self) -> Dict[str, List[str]]:
        """对所有文本字段 SELECT DISTINCT ... LIMIT 20"""
        global _field_values_cache, _field_values_loaded_at
        now = time.time()
        if (
            _field_values_cache is not None
            and _field_values_loaded_at
            and now - _field_values_loaded_at < _FIELD_VALUES_TTL
        ):
            return _field_values_cache

        values = {}
        for tbl, info in self.load_schema().items():
            for col in info["columns"]:
                t = col["type"].lower()
                if not any(kw in t for kw in ("varchar", "char", "text", "enum")):
                    continue
                try:
                    df = pd.read_sql(
                        f"SELECT DISTINCT `{col['name']}` FROM `{tbl}` "
                        f"WHERE `{col['name']}` IS NOT NULL LIMIT 20",
                        self.db.engine,
                    )
                    vals = [str(r[0]) for r in df.itertuples(index=False) if r[0] is not None]
                    if vals:
                        values[f"{tbl}.{col['name']}"] = vals
                except Exception:
                    pass

        _field_values_cache = values
        _field_values_loaded_at = time.time()
        return values

    # ========================================================================
    # Schema 搜索与精排
    # ========================================================================

    def search(self, query: str) -> List[str]:
        """关键词匹配搜索相关表"""
        schema = self.load_schema()
        if not schema:
            return []
        try:
            import jieba
            words = {w for w in jieba.lcut(query) if len(w) >= 2}
        except ImportError:
            words = set(query.lower().split())

        scored = []
        for tbl, info in schema.items():
            s = 0
            tl = tbl.lower()
            for w in words:
                if w in tl: s += 3
            for c in info["columns"]:
                cl = c["name"].lower()
                for w in words:
                    if w in cl: s += 1
            for c in info["columns"]:
                cm = (c.get("comment") or "").lower()
                for w in words:
                    if w in cm: s += 0.5
            if s > 0:
                scored.append((s, tbl))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[: self._top_k_search]]

    def rerank(self, query: str, candidates: List[str]) -> List[str]:
        if not candidates:
            return []
        if self.reranker:
            try:
                return self._rerank_model(query, candidates)
            except Exception:
                pass
        return self._rerank_tfidf(query, candidates)

    def _rerank_model(self, query: str, candidates: List[str]) -> List[str]:
        schema = self.load_schema()
        scored = []
        for tbl in candidates:
            ti = schema.get(tbl, {})
            desc = ", ".join(f"{c['name']}({c['type']})" for c in ti.get("columns", []))
            try:
                r = self.reranker.generate(f"判断表 '{tbl}' 与 '{query}' 的相关度（0-3）：{desc}")
                n = re.findall(r"[0-3]", r.strip())
                scored.append((int(n[0]) if n else 1, tbl))
            except Exception:
                scored.append((1, tbl))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[: self._top_k_rerank]]

    def _rerank_tfidf(self, query: str, candidates: List[str]) -> List[str]:
        try:
            import jieba
            qw = set(w for w in jieba.lcut(query.lower()) if len(w) >= 2)
        except ImportError:
            qw = set(query.lower().split())
        schema = self.load_schema()
        scored = []
        for tbl in candidates:
            txt = tbl.lower()
            for c in schema.get(tbl, {}).get("columns", []):
                txt += " " + c["name"].lower() + " " + (c.get("comment") or "").lower()
            scored.append((len(qw & set(txt.split())), tbl))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [t for _, t in scored[: self._top_k_rerank]]

    # ========================================================================
    # 增强 Schema 文本（每字段含注释 + 样本值）
    # ========================================================================

    def build_schema_text(self, query: str = "") -> str:
        """
        构建增强 Schema 文本，格式：

        📋 orders
          - amount(DOUBLE)  — 订单金额  e.g. 299.00, 1580.00
          - province(VARCHAR)  — 所在省份  e.g. 广东, 江苏

        Generator Agent 直接据此生成 SQL，不再需要任何同义词映射。
        """
        schema = self.load_schema()
        if not schema:
            return "（无表结构）"

        selected = (
            self.rerank(query, self.search(query))
            if query else list(schema.keys())[:3]
        )

        fv = self.load_field_values()
        lines = [f"【数据库表结构（{len(selected)} 张表）】"]
        for tbl in selected:
            lines.append(f"📋 {tbl}")
            for c in schema[tbl]["columns"]:
                s = f"  - {c['name']}({c['type']})"
                if c.get("is_pk"):
                    s += " 🔑PK"
                if c.get("comment"):
                    s += f"  — {c['comment']}"
                vals = fv.get(f"{tbl}.{c['name']}", [])
                if vals:
                    s += f"  e.g. {vals[0]}"
                    if len(vals) > 1:
                        s += f", {vals[1]}"
                lines.append(s)

        return "\n".join(lines)[:3000]
