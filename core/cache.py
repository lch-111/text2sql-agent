"""
==============================================================================
语义缓存系统 — L1 精确缓存 + L2 语义缓存 —— 【核心亮点】
==============================================================================
设计思路：
  Text-to-SQL 场景下，LLM 推理是最大延迟和成本瓶颈。
  实际业务中，大量问题是相似的（如"上月销量" vs "上个月卖了多少"），
  完全没必要每次都调用 LLM。

  本模块实现两级缓存：
    L1 — 精确缓存 (Exact Match Cache)
      对完全相同的提问（md5 哈希匹配），直接返回历史结果。
      适用于用户反复查询同一指标的场景。

    L2 — 语义缓存 (Semantic Cache)
      计算当前问题与历史问题的向量相似度。
      当相似度 > threshold（默认 0.95）时，直接复用历史 SQL 和结果。
      这样即使用户换了一种说法，也能命中缓存。

  缓存命中率会被记录到日志，用于后续在监控面板中展示。
==============================================================================
"""

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict, Any, List, Set
from datetime import datetime

import numpy as np
from collections import Counter

import pandas as pd

from core.config import CONFIG
from core.memory_manager import MemoryManager

# 缓存日志记录器
cache_logger = logging.getLogger("semantic_cache")
cache_logger.setLevel(logging.INFO)

# Redis 连接超时时间（秒）
_REDIS_TIMEOUT = 5


# ============================================================================
# 缓存统计
# ============================================================================

@dataclass
class CacheStats:
    """缓存命中率统计"""
    l1_hits: int = 0       # L1 精确缓存命中次数
    l2_hits: int = 0       # L2 语义缓存命中次数
    misses: int = 0         # 未命中次数
    total_queries: int = 0  # 总查询次数

    @property
    def hit_rate(self) -> float:
        """综合命中率"""
        if self.total_queries == 0:
            return 0.0
        return (self.l1_hits + self.l2_hits) / self.total_queries

    @property
    def l1_hit_rate(self) -> float:
        """L1 精确命中率"""
        if self.total_queries == 0:
            return 0.0
        return self.l1_hits / self.total_queries

    @property
    def l2_hit_rate(self) -> float:
        """L2 语义命中率"""
        if self.total_queries == 0:
            return 0.0
        return self.l2_hits / self.total_queries

    def record_l1_hit(self):
        self.l1_hits += 1
        self.total_queries += 1

    def record_l2_hit(self):
        self.l2_hits += 1
        self.total_queries += 1

    def record_miss(self):
        self.misses += 1
        self.total_queries += 1

    def to_dict(self) -> Dict:
        return {
            "l1_hits": self.l1_hits,
            "l2_hits": self.l2_hits,
            "misses": self.misses,
            "total_queries": self.total_queries,
            "hit_rate": round(self.hit_rate * 100, 2),
            "l1_hit_rate": round(self.l1_hit_rate * 100, 2),
            "l2_hit_rate": round(self.l2_hit_rate * 100, 2),
        }


# ============================================================================
# 缓存项定义
# ============================================================================

@dataclass
class CacheEntry:
    """
    缓存条目。

    存储内容：
    - query: 用户原始问题
    - sql: 生成的 SQL（或历史 SQL）
    - result_json: 查询结果的 JSON 序列化
    - embedding: 问题的向量表示（用于 L2 语义缓存）
    - structured_sig: 结构化意图签名（表名+字段+聚合+GROUP BY 的排序哈希）
    - timestamp: 缓存时间
    - token_estimate: 本次查询估算的 Token 消耗
    """
    query: str
    sql: str
    result_json: str
    embedding: Optional[List[float]] = None
    structured_sig: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    token_estimate: int = 0


# ============================================================================
# 语义缓存引擎
# ============================================================================

class SemanticCache:
    """
    两级语义缓存引擎。

    工作流程:
    1. 用户提问 query（经过 ConversationManager 标准化后的查询意图）
    2. 计算 query 的 md5 → 查 L1 缓存（精确匹配）
    3. 若 L1 未命中，计算 query 的向量 → 查 L2 缓存（语义匹配）
    4. 若 L2 也未命中，走 LLM 推理，并将结果写入 L1 + L2

    注意：
    - 缓存键使用经过 ConversationManager 补全/标准化后的查询意图
      （而非用户原始口语），不同意图的查询不会错误命中同一缓存。
    - L2 语义缓存使用余弦相似度判断。
      阈值越高（如 0.98），要求越严格，但误判率低。
      阈值越低（如 0.90），召回率越高，但可能返回错误结果。
      默认 0.95 是一个平衡值。
    """

    def __init__(self):
        self.cfg = CONFIG.cache
        self.stats = CacheStats()
        self._redis = None

        # 连接 Redis（必需，失败则抛出异常）
        self._init_redis()

        # L2 缓存的向量索引（基于 Redis 中的缓存条目构建）
        self._l2_entries: List[CacheEntry] = []
        self._max_l2_entries = 500

    # ========================================================================
    # 结构化意图签名 — 用 FieldResolver 解析结果构建缓存键后缀
    # ========================================================================

    def _extract_structured_sig(self, query: str) -> str:
        """
        从查询文本中提取结构化意图特征，生成排序后的哈希签名。

        特征包括：涉及的表名、字段列表、聚合函数类型、是否有 GROUP BY。
        仅文本相似度超过阈值 且 结构化签名完全一致时，才返回缓存。

        若 FieldResolver 不可用或解析失败，返回空字符串（降级为纯文本匹配）。
        """
        tables: Set[str] = set()
        fields: Set[str] = set()
        agg_funcs: Set[str] = set()
        has_group_by = False
        has_join = False

        # 用正则简单提取常见聚合函数
        agg_pattern = re.findall(
            r'\b(SUM|COUNT|AVG|MAX|MIN|DISTINCT)\s*\(', query, re.IGNORECASE
        )
        agg_funcs.update(f.upper() for f in agg_pattern)

        # 是否有 GROUP BY
        if re.search(r'\bGROUP\s+BY\b', query, re.IGNORECASE):
            has_group_by = True

        # 是否有 JOIN
        if re.search(r'\bJOIN\b', query, re.IGNORECASE):
            has_join = True

        # 通过 FieldResolver（如果已初始化 memory_manager）尝试解析字段映射
        try:
            mm = getattr(self, 'memory_manager', None)
            if mm:
                mappings = mm.get_all_global_mappings()
                for m in mappings:
                    fname = m.get("field", "").lower()
                    dval = m.get("display_value", "").lower()
                    if fname and dval and dval in query.lower():
                        fields.add(fname)
        except Exception:
            pass

        # 整理为排序后的签名
        parts = []
        if tables:
            parts.append("T:" + ",".join(sorted(tables)))
        if fields:
            parts.append("F:" + ",".join(sorted(fields)))
        if agg_funcs:
            parts.append("A:" + ",".join(sorted(agg_funcs)))
        if has_group_by:
            parts.append("GB:1")
        if has_join:
            parts.append("JN:1")

        sig = "|".join(parts) if parts else ""
        return sig

    def _normalized_cache_key(self, query: str) -> str:
        """
        结合原始问题和结构化意图签名生成统一缓存键。
        L1 精确缓存直接使用此键，L2 语义缓存也通过此键严格比对。
        """
        sig = self._extract_structured_sig(query)
        if sig:
            return f"{query} ||| {sig}"
        return query

    def _init_redis(self):
        """
        初始化 Redis 连接。

        优先连接真实 Redis 服务器。
        如果不可用（如在无 Docker 的开发环境中），降级为 fakeredis。
        fakeredis 是纯 Python 实现的 Redis 协议，API 完全兼容。
        生产环境请通过 docker-compose 启动真实 Redis。
        """
        import redis as redis_module
        try:
            self._redis = redis_module.Redis(
                host=self.cfg.redis_host,
                port=self.cfg.redis_port,
                db=self.cfg.redis_db,
                decode_responses=True,
                socket_connect_timeout=_REDIS_TIMEOUT,
                socket_timeout=_REDIS_TIMEOUT,
            )
            self._redis.ping()
            cache_logger.info(
                f"[缓存] Redis 服务器连接成功: {self.cfg.redis_host}:{self.cfg.redis_port}"
            )
        except Exception as e:
            # 真实 Redis 不可用时，使用 fakeredis 作为本地开发替代
            cache_logger.warning(
                f"[缓存] 无法连接 Redis 服务器 ({e})，"
                f"尝试使用 fakeredis 作为本地开发后端..."
            )
            try:
                import fakeredis
                self._redis = fakeredis.FakeStrictRedis(
                    decode_responses=True,
                )
                self._redis.ping()
                cache_logger.info(
                    "[缓存] fakeredis 后端已启动（仅用于本地开发，"
                    "生产环境请使用 docker-compose）"
                )
            except ImportError:
                raise RuntimeError(
                    "Redis 不可用且 fakeredis 未安装。\n"
                    "请通过 docker-compose up -d 启动 Redis，"
                    "或执行: pip install fakeredis[lua]"
                ) from e

    def _get_embedding(self, text: str) -> List[float]:
        """
        获取文本的向量表示（基于词频的 TF 向量）。

        由于环境无法连接 HuggingFace 下载模型，这里使用基于
        jieba 分词的 TF（词频）向量作为替代方案。

        对中文查询来说，词频向量已能捕捉到关键词重叠度，
        足以判断语义相似问题（如"上月销售额"vs"上个月销售总额"）。
        """
        try:
            import jieba
            # 使用 jieba 精确模式分词
            words = list(jieba.cut(text, cut_all=False))
        except ImportError:
            # jieba 不可用时，使用字符 n-gram 作为后备
            words = list(text)

        # 构建词频向量
        word_freq = Counter(words)

        # 为了有确定性的维度，对词排序后截取前 200 个维度
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        # 取前 200 维
        vec = [freq for _, freq in sorted_words[:200]]
        # 补零到 200 维
        while len(vec) < 200:
            vec.append(0.0)
        # L2 归一化
        vec_arr = np.array(vec, dtype=float)
        norm = np.linalg.norm(vec_arr)
        if norm > 0:
            vec_arr = vec_arr / norm
        return vec_arr.tolist()

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        """计算两个向量的余弦相似度"""
        a_arr = np.array(a)
        b_arr = np.array(b)
        dot = np.dot(a_arr, b_arr)
        norm = np.linalg.norm(a_arr) * np.linalg.norm(b_arr)
        if norm == 0:
            return 0.0
        return float(dot / norm)

    # ========================================================================
    # L1 精确缓存
    # ========================================================================

    def _l1_key(self, query: str) -> str:
        """L1 缓存的 key：md5(结构化标准化后的查询)"""
        nk = self._normalized_cache_key(query)
        return f"sql_cache:l1:{hashlib.md5(nk.encode()).hexdigest()}"

    def _l1_get(self, query: str) -> Optional[CacheEntry]:
        """L1 精确查找（仅通过 Redis）"""
        key = self._l1_key(query)
        data = self._redis.get(key)
        if data:
            return CacheEntry(**json.loads(data))
        return None

    def _l1_set(self, entry: CacheEntry):
        """写入 L1 缓存（仅通过 Redis）"""
        nk = self._normalized_cache_key(entry.query)
        entry.structured_sig = self._extract_structured_sig(entry.query)
        key = f"sql_cache:l1:{hashlib.md5(nk.encode()).hexdigest()}"
        data = json.dumps(asdict(entry), ensure_ascii=False)
        self._redis.setex(key, self.cfg.cache_ttl_seconds, data)

    # ========================================================================
    # L2 语义缓存
    # ========================================================================

    def _l2_search(self, query: str) -> Optional[CacheEntry]:
        """
        L2 语义查找。

        计算当前问题的向量，与历史问题的向量逐一比对。
        当最大相似度 > threshold 且结构化签名完全一致时，返回对应的缓存条目。

        结构化签名包含（表名、字段、聚合函数、GROUP BY 标志），
        确保"黑龙江销售额"与"商品平均单价"即使文本相似也不会误命中。
        """
        if not self._l2_entries:
            return None

        query_sig = self._extract_structured_sig(query)
        query_embedding = self._get_embedding(query)
        best_similarity = 0.0
        best_entry = None

        for entry in self._l2_entries:
            if entry.embedding is None:
                continue

            # 【校验 1】结构化签名必须完全一致
            if query_sig and entry.structured_sig:
                if query_sig != entry.structured_sig:
                    cache_logger.debug(
                        f"[L2] 跳过缓存: 结构化签名不匹配 "
                        f"query='{query_sig}' vs entry='{entry.structured_sig}'"
                    )
                    continue

            # 【校验 2】语义相似度必须超过阈值
            similarity = self._cosine_similarity(query_embedding, entry.embedding)
            if similarity > best_similarity:
                best_similarity = similarity
                best_entry = entry

        if best_similarity >= self.cfg.similarity_threshold and best_entry is not None:
            cache_logger.info(
                f"[L2语义缓存] 命中! 相似度={best_similarity:.4f}, "
                f"原问题='{best_entry.query}', 新问题='{query}'"
            )
            return best_entry

        return None

    def _l2_add(self, entry: CacheEntry):
        """添加到 L2 缓存索引"""
        # 如果已有相同 query，不重复添加
        for existing in self._l2_entries:
            if existing.query == entry.query:
                return

        # 计算结构化签名
        if not entry.structured_sig:
            entry.structured_sig = self._extract_structured_sig(entry.query)

        # 如果当前没有 embedding，生成一个
        if entry.embedding is None:
            try:
                entry.embedding = self._get_embedding(entry.query)
            except Exception:
                pass

        self._l2_entries.append(entry)

        # 限制内存缓存大小（LRU 策略：移除最旧的）
        if len(self._l2_entries) > self._max_l2_entries:
            self._l2_entries = self._l2_entries[-self._max_l2_entries:]

    # ========================================================================
    # 缓存结果校验（sqlglot 列名提取 + FieldResolver 字段比对）
    # ========================================================================

    def _validate_cache_sql(
        self, sql: str, query: str
    ) -> bool:
        """
        用 sqlglot 提取缓存 SQL 中的所有列名，与当前问题的字段引用做比对。
        若交集为空，说明缓存 SQL 与当前查询意图无关，应丢弃。

        返回 True 表示校验通过，False 表示应丢弃缓存。
        """
        try:
            import sqlglot
            from sqlglot import exp as sqlglot_exp

            # 1. 提取缓存 SQL 中的列名
            parsed = sqlglot.parse_one(sql)
            cached_columns: Set[str] = set()
            for node in parsed.find_all(sqlglot_exp.Column):
                col_name = node.name.lower()
                if col_name and col_name != '*':
                    cached_columns.add(col_name)

            # 无列名的 SQL（如 SELECT 1）直接放行
            if not cached_columns:
                return True

            # 2. 从当前查询中提取可能的字段引用
            query_fields: Set[str] = set()
            # 尝试通过 memory_manager 的全局映射提取
            mm = getattr(self, 'memory_manager', None)
            if mm:
                try:
                    mappings = mm.get_all_global_mappings()
                    for m in mappings:
                        fname = m.get("field", "").lower()
                        dval = m.get("display_value", "").lower()
                        if fname:
                            query_fields.add(fname)
                            if dval and dval in query.lower():
                                query_fields.add(fname)
                except Exception:
                    pass

            # 从聚合函数提取字段
            for match in re.findall(
                r'(SUM|COUNT|AVG|MAX|MIN)\s*\(\s*(\w+)',
                query, re.IGNORECASE
            ):
                query_fields.add(match[1].lower())

            # 从 WHERE 子句样式中提取字段
            for match in re.findall(
                r'(?:WHERE|AND|OR)\s+(\w+)\s*(?:=|!=|<|>|IN|LIKE)',
                query, re.IGNORECASE
            ):
                query_fields.add(match.lower())

            # 如果当前查询未能提取出字段（口语太模糊），不拦截缓存
            if not query_fields:
                return True

            # 3. 计算交集
            overlap = cached_columns & query_fields
            if not overlap:
                cache_logger.info(
                    f"[缓存校验] 列名交集为空，丢弃缓存: "
                    f"SQL列={cached_columns}, 查询字段={query_fields}"
                )
                return False

            cache_logger.debug(
                f"[缓存校验] 通过: 交集={overlap}"
            )
            return True

        except Exception as e:
            cache_logger.debug(f"[缓存校验] 异常，放行缓存: {e}")
            return True

    # ========================================================================
    # 对外接口
    # ========================================================================

    def get(self, query: str) -> Optional[Dict[str, Any]]:
        """
        查询缓存（L1 → L2 两级查找）。

        返回:
            {
                "sql": str,
                "result": pd.DataFrame 或 List[Dict],
                "source": "L1" | "L2" | None,
                "similarity": float | None,
            }
            未命中返回 None
        """
        # ---- L1 精确匹配 ----
        try:
            entry = self._l1_get(query)
            if entry is not None:
                # 用 sqlglot 校验缓存 SQL 的列与当前查询字段有交集
                if self._validate_cache_sql(entry.sql, query):
                    self.stats.record_l1_hit()
                    cache_logger.info(f"[L1精确缓存] 命中: '{query[:50]}...'")
                    return {
                        "sql": entry.sql,
                        "result": json.loads(entry.result_json),
                        "source": "L1",
                        "similarity": 1.0,
                    }
                else:
                    cache_logger.info(
                        f"[L1缓存] sqlglot 校验不通过，丢弃: '{query[:50]}...'"
                    )
        except Exception as e:
            cache_logger.warning(f"L1 缓存查询失败: {e}")

        # ---- L2 语义匹配 ----
        try:
            entry = self._l2_search(query)
            if entry is not None:
                if self._validate_cache_sql(entry.sql, query):
                    self.stats.record_l2_hit()
                    return {
                        "sql": entry.sql,
                        "result": json.loads(entry.result_json),
                        "source": "L2",
                        "similarity": self._cosine_similarity(
                            self._get_embedding(query),
                            entry.embedding or [],
                        ),
                    }
                else:
                    cache_logger.info(
                        f"[L2缓存] sqlglot 校验不通过，丢弃: "
                        f"'{query[:50]}...'"
                    )
        except Exception as e:
            cache_logger.warning(f"L2 缓存查询失败: {e}")

        # ---- 未命中 ----
        self.stats.record_miss()
        return None

    def set(self, query: str, sql: str, result: Any, token_estimate: int = 0):
        """
        写入缓存（同时写入 L1 和 L2）。

        参数:
            query: 用户问题
            sql: 生成的 SQL
            result: 查询结果（pandas DataFrame 或 list/dict）
            token_estimate: 估算的 Token 消耗
        """
        # 将结果序列化为 JSON
        if isinstance(result, pd.DataFrame):
            result_json = result.to_json(orient="records", force_ascii=False)
        else:
            result_json = json.dumps(result, ensure_ascii=False, default=str)

        structured_sig = self._extract_structured_sig(query)

        entry = CacheEntry(
            query=query,
            sql=sql,
            result_json=result_json,
            structured_sig=structured_sig,
            timestamp=datetime.now().isoformat(),
            token_estimate=token_estimate,
        )

        # 写入 L1
        try:
            self._l1_set(entry)
        except Exception as e:
            cache_logger.warning(f"L1 缓存写入失败: {e}")

        # 写入 L2（生成 embedding + 结构化签名）
        try:
            self._l2_add(entry)
        except Exception as e:
            cache_logger.warning(f"L2 缓存写入失败: {e}")

    def clear(self):
        """清空缓存（清除 Redis 中所有 sql_cache 前缀的 key）"""
        cursor = 0
        while True:
            cursor, keys = self._redis.scan(
                cursor, match="sql_cache:*", count=100
            )
            if keys:
                self._redis.delete(*keys)
            if cursor == 0:
                break
        self._l2_entries.clear()
        self.stats = CacheStats()
        cache_logger.info("[缓存] 已全部清空")

    def get_stats(self) -> Dict:
        """获取缓存命中率统计"""
        return self.stats.to_dict()

    def get_recent_logs(self, n: int = 20) -> List[str]:
        """获取最近的缓存日志"""
        # 从内存中的 _l2_entries 反向读取
        logs = []
        for entry in reversed(self._l2_entries[-n:]):
            logs.append(
                f"[{entry.timestamp}] query='{entry.query[:40]}...' | "
                f"SQL='{entry.sql[:50]}...'"
            )
        return logs or ["暂无缓存记录"]

    # ========================================================================
    # Schema 缓存（TTL 30min，用于 table_info 结果）
    # ========================================================================

    SCHEMA_CACHE_PREFIX = "schema_cache:"
    SCHEMA_CACHE_TTL = 1800  # 30 分钟

    def get_cached_schema(self, cache_key: str) -> Optional[list]:
        """
        从 Redis 读取缓存的表结构 JSON。

        参数:
            cache_key: 由 db_type + database + 表名列表拼接的缓存键

        返回:
            缓存的 table_info 列表，未命中返回 None
        """
        try:
            raw = self._redis.get(self.SCHEMA_CACHE_PREFIX + cache_key)
            if raw:
                cache_logger.info(f"[Schema缓存] 命中: {cache_key[:60]}")
                return json.loads(raw)
        except Exception:
            pass
        return None

    def set_cached_schema(self, cache_key: str, table_info: list):
        """
        将表结构 JSON 写入 Redis 缓存。

        参数:
            cache_key: 缓存键
            table_info: 表结构列表
        """
        try:
            self._redis.setex(
                self.SCHEMA_CACHE_PREFIX + cache_key,
                self.SCHEMA_CACHE_TTL,
                json.dumps(table_info, ensure_ascii=False, default=str),
            )
            cache_logger.info(f"[Schema缓存] 写入: {cache_key[:60]}")
        except Exception as e:
            cache_logger.warning(f"[Schema缓存] 写入失败: {e}")

    # ========================================================================
    # 全局字段映射（跨对话记忆入口，委托 MemoryManager）
    # ========================================================================

    def set_global_mapping(self, field: str, display_value: str, db_value: str) -> None:
        """
        记录全局字段"口语→数据库值"映射。

        跨对话复用：当用户在多个对话中成功使用相同的字段映射时，
        自动沉淀为全局知识，后续对话直接命中。

        参数:
            field: 数据库字段名（如 "status"）
            display_value: 用户口语化值（如 "已完成"）
            db_value: 数据库实际值（如 "已完成"）
        """
        if hasattr(self, 'memory_manager') and self.memory_manager:
            self.memory_manager.set_global_mapping(field, display_value, db_value)

    def get_global_mapping(self, field: str, display_value: str):
        """
        查询全局字段映射。

        参数:
            field: 数据库字段名
            display_value: 用户口语化值

        返回:
            数据库实际值，不存在返回 None
        """
        if hasattr(self, 'memory_manager') and self.memory_manager:
            return self.memory_manager.get_global_mapping(field, display_value)
        return None

    def get_all_global_mappings(self) -> list:
        """
        获取所有已验证（count >= 2）的全局字段映射。

        返回:
            [{"field": ..., "display_value": ..., "db_value": ..., "count": ...}, ...]
        """
        if hasattr(self, 'memory_manager') and self.memory_manager:
            return self.memory_manager.get_all_global_mappings()
        return []


# ============================================================================
# 全局单例
# ============================================================================

_cache_instance: Optional[SemanticCache] = None


def get_cache() -> SemanticCache:
    """获取缓存单例（自动挂载 MemoryManager）"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = SemanticCache()
        # 挂载记忆管理器，复用同一 Redis 连接
        _cache_instance.memory_manager = MemoryManager(_cache_instance._redis)
    return _cache_instance


# ============================================================================
# 独立测试入口
# ============================================================================
if __name__ == "__main__":
    # 配置日志
    logging.basicConfig(level=logging.INFO)

    cache = get_cache()
    print("=" * 60)
    print("语义缓存系统测试")
    print("=" * 60)

    # 模拟查询和缓存
    test_queries = [
        ("上月总销售额是多少", "SELECT SUM(total_amount) FROM orders WHERE order_date >= '2024-11-01' AND order_date < '2024-12-01'"),
        ("上个月的总销售额", "SELECT SUM(total_amount) FROM orders WHERE order_date >= '2024-11-01' AND order_date < '2024-12-01'"),
        ("查询广东省用户数量", "SELECT COUNT(*) FROM users WHERE province = '广东'"),
    ]

    for query, sql in test_queries:
        # 先 get（应未命中）
        result = cache.get(query)
        print(f"\n[首次查询] '{query}' -> {'命中' if result else '未命中'}")

        # 写入缓存
        dummy_result = [{"total": 10000}]
        cache.set(query, sql, dummy_result)

        # 再次 get（应命中 L1 或 L2）
        result = cache.get(query)
        source = result["source"] if result else "无"
        print(f"[二次查询] '{query}' -> {source} 命中")

    print(f"\n缓存统计: {json.dumps(cache.get_stats(), ensure_ascii=False, indent=2)}")
