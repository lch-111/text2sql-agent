"""
==============================================================================
混合检索模块 — BM25 (稀疏) + Embedding (稠密) + Rerank
==============================================================================
设计思路：
  Text-to-SQL 场景下，用户问题中的表名、字段名（如 "orders", "total_amount"）
  属于关键词精确匹配，适合 BM25 处理；而语义意图（如 "哪个省份的消费能力最强"）
  需要 Embedding 语义匹配。

  本模块实现了三个阶段的渐进式召回：
  1. BM25 召回：精确匹配表名/字段名，确保不遗漏关键上下文
  2. 向量召回：语义相关的 Schema 元素
  3. Rerank 重排序：对两种召回结果进行融合 + 相关性打分排序

  融合策略采用加权 RRF (Reciprocal Rank Fusion)：
    score = α * BM25_rank_weight + (1-α) * Vector_rank_weight
  其中 α 由 config.py 中的 bm25_weight 控制。
==============================================================================
"""

import json
import re
import math
from typing import List, Dict, Any, Optional, Tuple
from pathlib import Path

from core.config import CONFIG
from core.database import get_db


# ============================================================================
# BM25 全文检索（基于 rank-bm25）
# ============================================================================

class BM25Retriever:
    """
    基于 BM25 的稀疏检索器。

    对每个表和字段名构建索引，使得用户问题中的精确关键词
    （如 "orders", "total_amount", "用户"）能够直接匹配到相关 Schema。
    """

    def __init__(self):
        self._corpus: List[str] = []          # 文档原文
        self._doc_ids: List[str] = []         # 文档 ID
        self._metadata: List[Dict] = []       # 附加元数据
        self._bm25 = None
        self._tokenizer = self._default_tokenizer
        self._built = False

    @staticmethod
    def _default_tokenizer(text: str) -> List[str]:
        """
        默认分词器：支持中英文混排。

        策略：
        - 小写化英文
        - 按非字母数字字符拆分
        - 同时保留原始中文词组（英文是按单词，中文后续可接入 jieba）
        """
        text = text.lower()
        # 提取英文单词
        english_tokens = re.findall(r'[a-z_][a-z0-9_]*', text)
        # 提取中文词组（连续的中文字符）
        chinese_tokens = re.findall(r'[一-鿿]+', text)
        # 中文进一步拆单字以保证召回率
        chinese_chars = []
        for token in chinese_tokens:
            chinese_chars.extend(list(token))
        return english_tokens + chinese_tokens + chinese_chars

    def _build_schema_corpus(self) -> List[Tuple[str, str, Dict]]:
        """
        将数据库 Schema 展开成检索语料库。

        每张表会产生多个文档：
        - 表级文档：包含表名、DDL、注释
        - 字段级文档：每个字段单独作为一个文档

        这样设计可以让 "total_amount" 这样具体的字段名直接匹配到对应表。
        """
        db = get_db()
        tables_info = db.get_table_info()
        corpus_items = []

        for info in tables_info:
            table_name = info["table_name"]

            # ---- 表级文档 ----
            table_text_parts = [
                f"表名: {table_name}",
                f"表描述: 记录{_table_description(table_name)}",
            ]
            for col in info["columns"]:
                table_text_parts.append(f"字段: {col['name']} ({col['type']}) - {col['comment']}")

            table_text = " | ".join(table_text_parts)
            corpus_items.append((
                f"table_{table_name}",
                table_text,
                {"type": "table", "table_name": table_name},
            ))

            # ---- 字段级文档 ----
            for col in info["columns"]:
                field_text = (
                    f"表: {table_name} | "
                    f"字段: {col['name']} ({col['type']}) - {col['comment']} | "
                    f"字段名: {col['name']} | "
                    f"注释: {col['comment']}"
                )
                corpus_items.append((
                    f"field_{table_name}_{col['name']}",
                    field_text,
                    {
                        "type": "column",
                        "table_name": table_name,
                        "column_name": col["name"],
                        "column_type": col["type"],
                    },
                ))

        return corpus_items

    def build_index(self):
        """
        构建 BM25 索引。
        需在首次调用检索前执行。
        """
        corpus_items = self._build_schema_corpus()

        self._doc_ids = [item[0] for item in corpus_items]
        self._corpus = [item[1] for item in corpus_items]
        self._metadata = [item[2] for item in corpus_items]

        if not self._corpus:
            print("[BM25] 语料为空，请确认数据库已初始化。")
            return

        # 对文档进行分词
        tokenized_corpus = [self._tokenizer(doc) for doc in self._corpus]

        # 构建 BM25 模型（使用 BM25L 算法，对长文档更友好）
        try:
            from rank_bm25 import BM25Okapi
            self._bm25 = BM25Okapi(tokenized_corpus)
        except ImportError:
            print("[BM25] rank_bm25 未安装，使用简化 BM25 实现。")
            self._bm25 = _SimpleBM25(tokenized_corpus)

        self._built = True
        print(f"[BM25] 索引构建完成！共 {len(self._corpus)} 个文档。")

    def search(self, query: str, k: int = 10) -> List[Dict[str, Any]]:
        """
        执行 BM25 检索。

        参数:
            query: 用户自然语言问题
            k: 返回 top-k 结果

        返回:
            [{ "id": str, "content": str, "metadata": dict, "score": float }, ...]
        """
        if not self._built:
            self.build_index()

        tokenized_query = self._tokenizer(query)
        scores = self._bm25.get_scores(tokenized_query)

        # 获取 top-k 索引
        top_indices = sorted(
            range(len(scores)), key=lambda i: scores[i], reverse=True
        )[:k]

        results = []
        for idx in top_indices:
            if scores[idx] > 0:  # 丢弃得分为 0 的结果
                results.append({
                    "id": self._doc_ids[idx],
                    "content": self._corpus[idx],
                    "metadata": self._metadata[idx],
                    "score": scores[idx],
                })

        return results


# ============================================================================
# 简化版 BM25 实现（当 rank_bm25 不可用时的后备方案）
# ============================================================================

class _SimpleBM25:
    """简易 BM25 实现（仅用于 fallback）"""
    def __init__(self, tokenized_corpus: List[List[str]]):
        import math
        from collections import Counter

        self.corpus = tokenized_corpus
        self.n_docs = len(tokenized_corpus)
        self.avgdl = sum(len(d) for d in tokenized_corpus) / max(self.n_docs, 1)
        self.k1 = 1.5
        self.b = 0.75
        self.epsilon = 0.25

        # 计算 IDF
        self.idf = {}
        df = Counter()
        for doc in tokenized_corpus:
            for word in set(doc):
                df[word] += 1
        for word, freq in df.items():
            self.idf[word] = math.log(
                1 + (self.n_docs - freq + 0.5) / (freq + 0.5)
            )

    def get_scores(self, tokenized_query: List[str]) -> List[float]:
        scores = [0.0] * self.n_docs
        for word in tokenized_query:
            if word not in self.idf:
                continue
            idf_val = self.idf[word]
            for i, doc in enumerate(self.corpus):
                freq = doc.count(word)
                if freq > 0:
                    scores[i] += idf_val * (
                        freq * (self.k1 + 1)
                        / (freq + self.k1 * (1 - self.b + self.b * len(doc) / self.avgdl))
                    )
        return scores


# ============================================================================
# 重排序器 (Reranker)
# ============================================================================

class Reranker:
    """
    对 BM25 + 向量检索的混合结果进行重排序。

    使用 RRF (Reciprocal Rank Fusion) 融合策略：
      score(d) = α * (1 / (k + rank_bm25(d))) + (1-α) * (1 / (k + rank_vector(d)))

    其中 k=60 是稳定常数，α=bm25_weight 控制两种信号的权重。
    """

    @staticmethod
    def reciprocal_rank_fusion(
        bm25_results: List[Dict],
        vector_results: List[Dict],
        alpha: float = None,
        k: int = 60,
        top_n: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        RRF 融合与重排序。

        参数:
            bm25_results: BM25 检索结果
            vector_results: 向量检索结果
            alpha: BM25 权重（0~1），默认从配置读取
            k: RRF 稳定常数
            top_n: 最终返回的 top-n 结果

        返回:
            [{
                "table_name": str,
                "content": str,
                "bm25_score": float,
                "vector_score": float,
                "rrf_score": float,
                "source": "hybrid",
            }]
        """
        if alpha is None:
            alpha = CONFIG.vector_store.bm25_weight

        # 构建排名映射
        rank_map = {}  # key: table_name -> {"bm25_rank": int, "vector_rank": int, ...}

        for rank, item in enumerate(bm25_results):
            tbl = item.get("metadata", {}).get("table_name") or item.get("table_name", "unknown")
            if tbl not in rank_map:
                rank_map[tbl] = {
                    "table_name": tbl,
                    "content_bm25": item.get("content", ""),
                    "content_vector": "",
                    "bm25_score": item.get("score", 0),
                    "vector_score": 0,
                }
            rank_map[tbl]["bm25_rank"] = rank + 1
            rank_map[tbl]["content_bm25"] = item.get("content", "")

        for rank, item in enumerate(vector_results):
            tbl = item.get("table_name", "unknown")
            if tbl not in rank_map:
                rank_map[tbl] = {
                    "table_name": tbl,
                    "content_bm25": "",
                    "content_vector": item.get("content", ""),
                    "bm25_score": 0,
                    "vector_score": item.get("score", 0),
                }
            rank_map[tbl]["vector_rank"] = rank + 1
            rank_map[tbl]["vector_score"] = item.get("score", 0)
            if not rank_map[tbl]["content_vector"]:
                rank_map[tbl]["content_vector"] = item.get("content", "")

        # 计算 RRF 得分
        for tbl, info in rank_map.items():
            bm25_rank = info.get("bm25_rank")
            vector_rank = info.get("vector_rank")

            rrf_score = 0.0
            if bm25_rank:
                rrf_score += alpha * (1 / (k + bm25_rank))
            if vector_rank:
                rrf_score += (1 - alpha) * (1 / (k + vector_rank))

            info["rrf_score"] = rrf_score
            # 选择内容更丰富的
            info["content"] = info["content_vector"] or info["content_bm25"]

        # 按 RRF 得分排序
        sorted_items = sorted(
            rank_map.values(),
            key=lambda x: x["rrf_score"],
            reverse=True,
        )

        return sorted_items[:top_n]


# ============================================================================
# RRFRetriever — 正式的 RRF 重排序检索器
# ============================================================================

class RRFRetriever:
    """
    正式的 RRF (Reciprocal Rank Fusion) 检索器。

    通过依赖注入接收 BM25 检索器和向量检索器，对两者结果进行
    加权 RRF 融合后返回最终排序结果。

    使用方法:
        bm25 = BM25Retriever()
        vector = SchemaVectorStore()
        retriever = RRFRetriever(bm25_retriever=bm25, vector_retriever=vector)
        results = retriever.retrieve("上月销售额", k=5)
    """

    def __init__(
        self,
        bm25_retriever: Optional[BM25Retriever] = None,
        vector_retriever: Optional["SchemaVectorStore"] = None,
        alpha: Optional[float] = None,
        k_constant: int = 60,
    ):
        """
        参数:
            bm25_retriever: BM25 稀疏检索器实例
            vector_retriever: 向量稠密检索器实例
            alpha: BM25 权重（0~1），默认从配置读取
            k_constant: RRF 稳定常数（默认 60）
        """
        if bm25_retriever is None:
            bm25_retriever = BM25Retriever()
        if vector_retriever is None:
            from core.vector_store import SchemaVectorStore
            vector_retriever = SchemaVectorStore()

        self.bm25_retriever = bm25_retriever
        self.vector_retriever = vector_retriever
        self.alpha = alpha if alpha is not None else CONFIG.vector_store.bm25_weight
        self.k_constant = k_constant
        self._initialized = False

    def initialize(self):
        """初始化 BM25 索引和向量库"""
        if not self._initialized:
            try:
                self.bm25_retriever.build_index()
            except Exception as e:
                print(f"[RRFRetriever] BM25 索引构建失败: {e}")
            try:
                self.vector_retriever.build_index()
            except Exception as e:
                print(f"[RRFRetriever] 向量库索引构建失败: {e}")
            self._initialized = True

    def reciprocal_rank_fusion(
        self,
        bm25_results: List[Dict],
        vector_results: List[Dict],
        top_n: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        对 BM25 和向量检索结果进行 RRF 融合。

        融合公式:
            score(d) = α * (1/(k + rank_bm25(d))) + (1-α) * (1/(k + rank_vector(d)))

        参数:
            bm25_results: BM25 检索结果列表
            vector_results: 向量检索结果列表
            top_n: 最终返回的 top-n 结果

        返回:
            [{
                "table_name": str,
                "content": str,
                "bm25_score": float,
                "vector_score": float,
                "rrf_score": float,
                "source": "hybrid",
            }, ...]
        """
        rank_map: Dict[str, Dict] = {}

        # ---- 构建 BM25 排名映射 ----
        for rank, item in enumerate(bm25_results):
            tbl = item.get("metadata", {}).get("table_name") or item.get("table_name", "unknown")
            if tbl not in rank_map:
                rank_map[tbl] = {
                    "table_name": tbl,
                    "content_bm25": "",
                    "content_vector": "",
                    "bm25_score": 0.0,
                    "vector_score": 0.0,
                }
            rank_map[tbl]["bm25_rank"] = rank + 1
            rank_map[tbl]["bm25_score"] = item.get("score", 0)
            rank_map[tbl]["content_bm25"] = item.get("content", "")

        # ---- 构建向量排名映射 ----
        for rank, item in enumerate(vector_results):
            tbl = item.get("table_name", "unknown")
            if tbl not in rank_map:
                rank_map[tbl] = {
                    "table_name": tbl,
                    "content_bm25": "",
                    "content_vector": "",
                    "bm25_score": 0.0,
                    "vector_score": 0.0,
                }
            rank_map[tbl]["vector_rank"] = rank + 1
            rank_map[tbl]["vector_score"] = item.get("score", 0)
            if not rank_map[tbl].get("content_vector"):
                rank_map[tbl]["content_vector"] = item.get("content", "")

        # ---- 计算 RRF 综合得分 ----
        k = self.k_constant
        alpha = self.alpha
        for tbl, info in rank_map.items():
            bm25_rank = info.get("bm25_rank")
            vector_rank = info.get("vector_rank")

            rrf_score = 0.0
            if bm25_rank:
                rrf_score += alpha * (1.0 / (k + bm25_rank))
            if vector_rank:
                rrf_score += (1.0 - alpha) * (1.0 / (k + vector_rank))

            info["rrf_score"] = rrf_score
            info["content"] = info.get("content_vector") or info.get("content_bm25", "")
            info["source"] = "hybrid"

        # ---- 按 RRF 得分降序排列 ----
        sorted_items = sorted(
            rank_map.values(),
            key=lambda x: x["rrf_score"],
            reverse=True,
        )

        return sorted_items[:top_n]

    def retrieve(self, query: str, k: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        执行完整的混合检索链路：BM25 → 向量 → RRF 融合。

        参数:
            query: 用户自然语言问题
            k: 返回 top-k 结果（默认使用配置值）

        返回:
            [{ table_name, content, bm25_score, vector_score, rrf_score }, ...]
        """
        self.initialize()
        if k is None:
            k = CONFIG.vector_store.top_k_schemas

        # Step 1: BM25 稀疏检索（召回数 = k * 3 以扩大候选池）
        bm25_results = self.bm25_retriever.search(query, k=k * 3)

        # Step 2: 向量稠密检索
        vector_results = self.vector_retriever.similarity_search(query, k=k * 3)

        # Step 3: RRF 融合重排序
        fused = self.reciprocal_rank_fusion(bm25_results, vector_results, top_n=k)

        return fused

    def retrieve_formatted(self, query: str) -> str:
        """
        检索并以格式化文本返回（用于直接注入 Prompt）。

        返回包含 Schema 和字段描述的结构化文本。
        """
        results = self.retrieve(query)
        if not results:
            return ""

        lines = ["【相关数据库表结构】"]
        for i, r in enumerate(results, 1):
            lines.append(f"\n--- 表 {i}: {r['table_name']} ---")
            content_lines = r.get("content", "").split("\n")
            for cl in content_lines[:8]:
                lines.append(f"  {cl}")

        return "\n".join(lines)


# ============================================================================
# 混合检索器（统一入口）
# ============================================================================

class HybridRetriever:
    """
    混合检索统一入口。

    使用方法:
        retriever = HybridRetriever()
        results = retriever.retrieve("查询用户的订单总金额")
    """

    def __init__(self):
        from core.vector_store import SchemaVectorStore

        self.bm25 = BM25Retriever()
        self.vector_store = SchemaVectorStore()
        self.reranker = Reranker()
        self._initialized = False

    def initialize(self):
        """初始化 BM25 索引和向量库"""
        if not self._initialized:
            try:
                self.bm25.build_index()
            except Exception as e:
                print(f"[混合检索] BM25 索引构建失败: {e}")
            try:
                self.vector_store.build_index()
            except Exception as e:
                print(f"[混合检索] 向量库索引构建失败: {e}")
            self._initialized = True

    def retrieve(self, query: str, k: int = None) -> List[Dict[str, Any]]:
        """
        执行混合检索三步曲：
        1. BM25 精确匹配
        2. 向量语义检索
        3. RRF 重排序

        参数:
            query: 用户自然语言问题
            k: 最终返回的结果数量

        返回:
            [{ table_name, content, rrf_score, ... }]
        """
        self.initialize()
        if k is None:
            k = CONFIG.vector_store.top_k_schemas

        # Step 1: BM25 检索
        bm25_results = self.bm25.search(query, k=k * 3)

        # Step 2: 向量语义检索
        vector_results = self.vector_store.similarity_search(query, k=k * 3)

        # Step 3: RRF 融合重排序
        fused = self.reranker.reciprocal_rank_fusion(
            bm25_results, vector_results, top_n=k,
        )

        return fused

    def retrieve_formatted(self, query: str) -> str:
        """
        检索并以格式化文本返回（用于直接注入 Prompt）。

        返回包含 Schema 和字段描述的结构化文本。
        """
        results = self.retrieve(query)
        if not results:
            return ""

        lines = ["【相关数据库表结构】"]
        for i, r in enumerate(results, 1):
            lines.append(f"\n--- 表 {i}: {r['table_name']} ---")
            # 解构 content 只取关键信息
            content_lines = r.get("content", "").split("\n")
            # 取前 5 行（表名 + 字段）
            for cl in content_lines[:8]:
                lines.append(f"  {cl}")

        return "\n".join(lines)


# ============================================================================
# 工具函数
# ============================================================================

def _table_description(table_name: str) -> str:
    """获取表的中文描述"""
    descriptions = {
        "users": "用户基本信息，包含注册信息、会员等级和地理位置",
        "products": "商品信息，包含商品名称、类别、价格和库存",
        "orders": "订单交易记录，包含下单用户、商品、金额、状态和支付方式",
    }
    return descriptions.get(table_name, table_name)


# ============================================================================
# 独立测试入口
# ============================================================================
if __name__ == "__main__":
    retriever = HybridRetriever()
    retriever.initialize()

    test_queries = [
        "哪些用户的订单金额最高",
        "广东省的销售总额是多少",
        "电子产品的平均价格",
        "统计每个月的订单数量",
    ]

    for q in test_queries:
        print(f"\n{'='*60}")
        print(f"[查询]: {q}")
        print(f"{'='*60}")
        results = retriever.retrieve(q)
        print(f"{'查询结果':_<60}")
        for r in results:
            print(f"  表: {r['table_name']:12s} | RRF得分: {r['rrf_score']:.4f}")
            preview = r['content'][:120].replace("\n", " | ")
            print(f"  内容: {preview}...")
