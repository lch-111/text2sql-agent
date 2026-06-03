"""
==============================================================================
向量存储模块 — 基于 TF-IDF 的 Schema 语义检索
==============================================================================
设计思路：
  由于环境无法连接 HuggingFace/ONNX 下载 embedding 模型，
  改用以下方案实现语义检索：

  1. 使用 jieba 对中文 Schema 文档进行分词
  2. 构建 TF（词频）向量作为文档表示
  3. 查询时同样分词 → 向量化 → 余弦相似度排序

  效果虽然不如 Transformer embedding 精确，但结合 BM25 关键词匹配
  和 RRF 重排序后，整体混合检索质量依然可靠。
==============================================================================
"""

import json
import math
from typing import List, Dict, Any, Optional
from pathlib import Path
from collections import Counter, defaultdict

import numpy as np

from config import CONFIG
from database import get_db


# ============================================================================
# 分词器
# ============================================================================

def _tokenize(text: str) -> List[str]:
    """
    中文分词 + 英文关键词提取。

    优先使用 jieba 分词，如不可用则降级为字符级分词。
    """
    import re
    # 英文单词和数字
    english_tokens = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', text.lower())
    # 中文部分
    chinese_part = re.findall(r'[一-鿿]+', text)
    try:
        import jieba
        chinese_tokens = []
        for chunk in chinese_part:
            chinese_tokens.extend(jieba.lcut(chunk))
    except ImportError:
        # jieba 不可用时，按单字切分
        chinese_tokens = list(''.join(chinese_part))

    return english_tokens + chinese_tokens


# ============================================================================
# TF-IDF 向量化器
# ============================================================================

class TfidfVectorizer:
    """
    简化的 TF-IDF 向量化器。

    与 sklearn 不同，此实现完全自包含，无需额外依赖。
    """

    def __init__(self):
        self.idf: Dict[str, float] = {}
        self._fitted = False

    def fit(self, documents: List[str]):
        """计算 IDF"""
        n_docs = len(documents)
        df = Counter()
        for doc in documents:
            tokens = set(_tokenize(doc))
            for token in tokens:
                df[token] += 1

        self.idf = {}
        for token, freq in df.items():
            self.idf[token] = math.log((n_docs - freq + 0.5) / (freq + 0.5) + 1.0)
        self._fitted = True
        return self

    def transform(self, documents: List[str]) -> np.ndarray:
        """将文档列表转换为 TF-IDF 矩阵"""
        if not self._fitted:
            raise RuntimeError("请先调用 fit()")

        matrix = []
        for doc in documents:
            tokens = _tokenize(doc)
            tf = Counter(tokens)
            max_tf = max(tf.values()) if tf else 1
            vec = np.zeros(len(self.idf))
            for i, (token, _) in enumerate(sorted(self.idf.items())):
                if token in tf:
                    tf_val = tf[token] / max_tf  # 归一化词频
                    vec[i] = tf_val * self.idf[token]
            matrix.append(vec)
        return np.array(matrix)

    def transform_single(self, text: str) -> np.ndarray:
        """将单个文本转换为 TF-IDF 向量（1 x N 矩阵）"""
        return self.transform([text])


# ============================================================================
# Schema 向量存储
# ============================================================================

class SchemaVectorStore:
    """
    基于 TF-IDF 的 Schema 向量存储。

    使用方法:
        store = SchemaVectorStore()
        store.build_index()
        results = store.similarity_search("查询用户订单金额", k=3)
    """

    def __init__(self):
        self.documents: List[str] = []
        self.metadatas: List[Dict] = []
        self.ids: List[str] = []
        self.vectorizer = TfidfVectorizer()
        self.vectors: Optional[np.ndarray] = None
        self._built = False

    # ========================================================================
    # Schema 文本构建
    # ========================================================================

    def _build_schema_document(self, table_info: Dict[str, Any]) -> str:
        """将表结构信息构建为结构化的文本描述"""
        lines = [f"表名: {table_info['table_name']}", f"行数: {table_info['row_count']}", "字段:"]
        for col in table_info["columns"]:
            lines.append(f"  - {col['name']} ({col['type']}): {col['comment']}")
        if table_info.get("distinct_stats"):
            lines.append("类别字段取值分布:")
            for field, stats in table_info["distinct_stats"].items():
                top_values = list(stats.keys())[:5]
                lines.append(f"  {field}: {', '.join(str(v) for v in top_values)}")
        if table_info.get("sample_rows"):
            lines.append("示例数据:")
            for row in table_info["sample_rows"][:2]:
                lines.append(f"  {json.dumps(row, ensure_ascii=False)}")
        return "\n".join(lines)

    # ========================================================================
    # 索引构建
    # ========================================================================

    def build_index(self):
        """从数据库提取元数据，构建 TF-IDF 向量索引"""
        db = get_db()
        tables_info = db.get_table_info()

        self.documents = []
        self.metadatas = []
        self.ids = []

        for info in tables_info:
            doc_text = self._build_schema_document(info)
            self.documents.append(doc_text)
            self.metadatas.append({
                "table_name": info["table_name"],
                "row_count": info["row_count"],
                "primary_key": info.get("primary_key", ""),
            })
            self.ids.append(f"schema_{info['table_name']}")

        # 构建 TF-IDF 向量索引
        self.vectorizer.fit(self.documents)
        self.vectors = self.vectorizer.transform(self.documents)
        self._built = True
        print(f"[向量库] TF-IDF 索引构建完成！共 {len(self.ids)} 张表。")

    # ========================================================================
    # 检索接口
    # ========================================================================

    def similarity_search(self, query: str, k: int = None) -> List[Dict[str, Any]]:
        """
        语义检索：用余弦相似度找最相关的表结构。

        参数:
            query: 用户的自然语言问题
            k: 返回结果数量（默认使用配置值）

        返回:
            [{ "table_name": str, "content": str, "score": float }, ...]
        """
        if k is None:
            k = CONFIG.vector_store.top_k_schemas

        if not self._built:
            print("[向量库] 索引为空，请先调用 build_index()")
            return []

        # 对查询向量化
        query_vec = self.vectorizer.transform_single(query)

        # 计算余弦相似度
        similarities = []
        for i, doc_vec in enumerate(self.vectors):
            dot = np.dot(query_vec[0], doc_vec)
            norm = np.linalg.norm(query_vec[0]) * np.linalg.norm(doc_vec)
            sim = float(dot / norm) if norm > 0 else 0.0
            similarities.append((i, sim))

        # 排序并取 top-k
        similarities.sort(key=lambda x: x[1], reverse=True)
        top_k = similarities[:min(k, len(similarities))]

        output = []
        for idx, score in top_k:
            output.append({
                "table_name": self.metadatas[idx].get("table_name", "unknown"),
                "content": self.documents[idx],
                "score": score,
            })

        return output

    def get_all_schemas(self) -> str:
        """获取所有表的完整 Schema 文本"""
        return "\n\n---\n\n".join(self.documents) if self.documents else ""


# ============================================================================
# 便捷函数
# ============================================================================

def init_vector_store():
    """一键初始化向量库"""
    store = SchemaVectorStore()
    store.build_index()
    return store


# ============================================================================
# 独立测试入口
# ============================================================================
if __name__ == "__main__":
    store = init_vector_store()
    test_queries = [
        "查询用户的订单总金额",
        "统计各省份销售情况",
        "查看商品类别分布",
    ]
    for q in test_queries:
        print(f"\n查询: {q}")
        results = store.similarity_search(q, k=3)
        for r in results:
            print(f"  表: {r['table_name']:12s} 相似度: {r['score']:.4f}")
