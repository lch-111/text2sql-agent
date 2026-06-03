"""
==============================================================================
配置文件 — 集中管理所有可调参数
==============================================================================
设计思路：所有环境变量和超参数集中在单一配置类中，方便调优与审计。
         同时支持通过环境变量覆盖默认值，满足不同部署环境的需求。
==============================================================================
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional


# ============================================================================
# 数据库配置
# ============================================================================
@dataclass
class DatabaseConfig:
    """数据库连接配置（支持 SQLite / PostgreSQL / MySQL）"""
    # 数据库类型: "sqlite" | "postgres" | "mysql"
    db_type: str = os.getenv("DB_TYPE", "sqlite")
    # SQLite 配置
    db_path: str = os.getenv("DB_PATH", "data/retail_warehouse.db")
    # PostgreSQL 配置（当 db_type="postgres" 时使用）
    pg_host: str = os.getenv("PG_HOST", "localhost")
    pg_port: int = int(os.getenv("PG_PORT", "5432"))
    pg_database: str = os.getenv("PG_DATABASE", "text2sql")
    pg_user: str = os.getenv("PG_USER", "text2sql")
    pg_password: str = os.getenv("PG_PASSWORD", "text2sql_secret")
    # MySQL 配置（当 db_type="mysql" 时使用）
    mysql_host: str = os.getenv("MYSQL_HOST", "localhost")
    mysql_port: int = int(os.getenv("MYSQL_PORT", "3306"))
    mysql_database: str = os.getenv("MYSQL_DATABASE", "text2sql")
    mysql_user: str = os.getenv("MYSQL_USER", "root")
    mysql_password: str = os.getenv("MYSQL_PASSWORD", "")
    # 在初始化时自动创建多少条模拟数据
    seed_data_count: int = 1000


# ============================================================================
# 向量存储 & 混合检索配置
# ============================================================================
@dataclass
class VectorStoreConfig:
    """向量库配置（基于 TF-IDF + jieba 的本地语义检索）"""
    # 混合检索时召回的 top-k 条 Schema
    top_k_schemas: int = 5
    # BM25 权重（α），语义检索权重为 1-α
    bm25_weight: float = 0.3


# ============================================================================
# 语义缓存配置
# ============================================================================
@dataclass
class SemanticCacheConfig:
    """两级语义缓存配置"""
    redis_host: str = os.getenv("REDIS_HOST", "localhost")
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_db: int = int(os.getenv("REDIS_DB", "0"))
    # L2 语义缓存相似度阈值（0 ~ 1），越接近 1 要求越严格
    similarity_threshold: float = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.9"))
    # 缓存 TTL（秒），默认 1 小时
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))


# ============================================================================
# LLM 配置
# ============================================================================
@dataclass
class LLMConfig:
    """大语言模型调用配置"""
    # 使用 Ollama 本地模型（默认），也可切换为 OpenAI
    provider: str = os.getenv("LLM_PROVIDER", "ollama")
    # Ollama 配置
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    # OpenAI 配置（通过环境变量注入 API Key）
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    # LLM 调用参数
    temperature: float = 0.2  # SQL 生成需要低温度保证确定性
    max_tokens: int = 1000


# ============================================================================
# 多辅助模型配置 — 各任务用最适合的免费模型
# ============================================================================
@dataclass
class AuxModelSpec:
    """单个辅助模型的规格"""
    api_key: str = ""
    base_url: str = ""
    model: str = ""
    temperature: float = 0.1
    max_tokens: int = 300


@dataclass
class AuxLLMConfig:
    """
    多辅助模型配置 — 每个任务可指定不同的免费模型。

    查找优先级: 任务专用模型 (intent/validate/explain)
              → 默认模型 (default)
              → 主模型 (llm)
    """
    # 默认辅助模型（所有任务回退）
    default_api_key: str = os.getenv("AUX_DEFAULT_API_KEY", "")
    default_base_url: str = os.getenv("AUX_DEFAULT_BASE_URL", "")
    default_model: str = os.getenv("AUX_DEFAULT_MODEL", "qwen-turbo")

    # 意图识别专用（中文理解强 → 推荐 GLM-4-Flash 或 qwen-turbo）
    intent_model: str = os.getenv("AUX_INTENT_MODEL", "")
    intent_api_key: str = os.getenv("AUX_INTENT_API_KEY", "")
    intent_base_url: str = os.getenv("AUX_INTENT_BASE_URL", "")

    # SQL 验证专用（推理准确 → 推荐 qwen-turbo 或 Qwen2.5-7B）
    validate_model: str = os.getenv("AUX_VALIDATE_MODEL", "")
    validate_api_key: str = os.getenv("AUX_VALIDATE_API_KEY", "")
    validate_base_url: str = os.getenv("AUX_VALIDATE_BASE_URL", "")

    # 结果解释专用（文本生成好 → 推荐 qwen-turbo 或 GLM-4-Flash）
    explain_model: str = os.getenv("AUX_EXPLAIN_MODEL", "")
    explain_api_key: str = os.getenv("AUX_EXPLAIN_API_KEY", "")
    explain_base_url: str = os.getenv("AUX_EXPLAIN_BASE_URL", "")

    def get_spec(self, task: str) -> AuxModelSpec:
        """获取指定任务的模型规格（优先级：任务专用 > 默认 > 主模型）"""
        key_map = {
            "intent": (self.intent_model, self.intent_api_key, self.intent_base_url),
            "sql_validate": (self.validate_model, self.validate_api_key, self.validate_base_url),
            "explain": (self.explain_model, self.explain_api_key, self.explain_base_url),
        }
        task_model, task_key, task_url = key_map.get(task, ("", "", ""))
        return AuxModelSpec(
            model=task_model or self.default_model,
            api_key=task_key or self.default_api_key or CONFIG.llm.openai_api_key,
            base_url=task_url or self.default_base_url or CONFIG.llm.openai_base_url,
        )


# ============================================================================
# Agent 配置
# ============================================================================
@dataclass
class AgentConfig:
    """Agent 执行链路配置"""
    # 最大自我修正轮数
    max_retries: int = 2
    # 是否启用 Few-Shot 动态检索
    enable_few_shot: bool = True
    # 注入到 Prompt 中的 Few-Shot 示例数量
    few_shot_count: int = 3


# ============================================================================
# 文件处理配置
# ============================================================================
@dataclass
class FileProcessingConfig:
    """多模态文件处理配置"""
    # 上传文件大小限制（MB）
    max_upload_size_mb: int = 10
    # 支持的文件格式
    allowed_extensions: List[str] = field(default_factory=lambda: [
        ".pdf", ".xlsx", ".xls", ".csv",
    ])
    # PDF 解析引擎: "pypdf2" | "pdfplumber"
    pdf_engine: str = "pypdf2"
    # 文件缓存目录
    upload_dir: str = "data/uploads"


# ============================================================================
# LangSmith 链路追踪配置
# ============================================================================
@dataclass
class TracingConfig:
    """LangSmith 链路追踪配置"""
    enabled: bool = os.getenv("LANGSMITH_TRACING", "false").lower() == "true"
    api_key: str = os.getenv("LANGSMITH_API_KEY", "")
    project: str = os.getenv("LANGSMITH_PROJECT", "text2sql-agent")
    endpoint: str = os.getenv(
        "LANGSMITH_ENDPOINT", "https://api.smith.langchain.com"
    )


# ============================================================================
# 评估配置
# ============================================================================
@dataclass
class EvaluationConfig:
    """自动化评估流水线配置"""
    # Golden Dataset 路径
    golden_dataset_path: str = "data/golden_dataset.json"
    # 评估结果输出路径
    report_path: str = "eval_results/eval_report.json"
    # 评估框架: "ragas" | "deepeval"
    framework: str = "deepeval"


# ============================================================================
# 主配置入口
# ============================================================================
@dataclass
class AppConfig:
    """应用全局配置"""
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"
    db: DatabaseConfig = field(default_factory=DatabaseConfig)
    vector_store: VectorStoreConfig = field(default_factory=VectorStoreConfig)
    cache: SemanticCacheConfig = field(default_factory=SemanticCacheConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    aux_llm: AuxLLMConfig = field(default_factory=AuxLLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    file_processing: FileProcessingConfig = field(default_factory=FileProcessingConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)
    eval: EvaluationConfig = field(default_factory=EvaluationConfig)


# 全局单例配置
CONFIG = AppConfig()
