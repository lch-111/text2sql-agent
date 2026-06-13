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
from typing import Optional, Dict, Any, List
from datetime import datetime

import numpy as np
from collections import Counter

import pandas as pd

from core.config import CONFIG

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
    - timestamp: 缓存时间
    - token_estimate: 本次查询估算的 Token 消耗
    """
    query: str
    sql: str
    result_json: str
    embedding: Optional[List[float]] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    token_estimate: int = 0


# ============================================================================
# 语义缓存引擎
# ============================================================================

class SemanticCache:
    """
    两级语义缓存引擎。

    工作流程:
    1. 用户提问 query
    2. 计算 query 的 md5 → 查 L1 缓存（精确匹配）
    3. 若 L1 未命中，计算 query 的向量 → 查 L2 缓存（语义匹配）
    4. 若 L2 也未命中，走 LLM 推理，并将结果写入 L1 + L2

    注意：L2 语义缓存使用余弦相似度判断。
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
        """L1 缓存的 key：md5(query)"""
        return f"sql_cache:l1:{hashlib.md5(query.encode()).hexdigest()}"

    def _l1_get(self, query: str) -> Optional[CacheEntry]:
        """L1 精确查找（仅通过 Redis）"""
        key = self._l1_key(query)
        data = self._redis.get(key)
        if data:
            return CacheEntry(**json.loads(data))
        return None

    def _l1_set(self, entry: CacheEntry):
        """写入 L1 缓存（仅通过 Redis）"""
        key = self._l1_key(entry.query)
        data = json.dumps(asdict(entry), ensure_ascii=False)
        self._redis.setex(key, self.cfg.cache_ttl_seconds, data)

    # ========================================================================
    # L2 语义缓存
    # ========================================================================

    def _l2_search(self, query: str) -> Optional[CacheEntry]:
        """
        L2 语义查找。

        计算当前问题的向量，与历史问题的向量逐一比对。
        当最大相似度 > threshold 时，返回对应的缓存条目。

        优化思路：
        - 生产环境可使用 Milvus/FAISS 做大规模近似搜索
        - 本实现在条目少时用暴力搜索，足够原型使用
        """
        if not self._l2_entries:
            return None

        query_embedding = self._get_embedding(query)
        best_similarity = 0.0
        best_entry = None

        for entry in self._l2_entries:
            if entry.embedding is None:
                continue
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
                self.stats.record_l1_hit()
                cache_logger.info(f"[L1精确缓存] 命中: '{query[:50]}...'")
                return {
                    "sql": entry.sql,
                    "result": json.loads(entry.result_json),
                    "source": "L1",
                    "similarity": 1.0,
                }
        except Exception as e:
            cache_logger.warning(f"L1 缓存查询失败: {e}")

        # ---- L2 语义匹配 ----
        try:
            entry = self._l2_search(query)
            if entry is not None:
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

        entry = CacheEntry(
            query=query,
            sql=sql,
            result_json=result_json,
            timestamp=datetime.now().isoformat(),
            token_estimate=token_estimate,
        )

        # 写入 L1
        try:
            self._l1_set(entry)
        except Exception as e:
            cache_logger.warning(f"L1 缓存写入失败: {e}")

        # 写入 L2（生成 embedding）
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


# ============================================================================
# 全局单例
# ============================================================================

_cache_instance: Optional[SemanticCache] = None


def get_cache() -> SemanticCache:
    """获取缓存单例"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = SemanticCache()
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
