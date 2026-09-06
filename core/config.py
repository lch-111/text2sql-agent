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
    # （数据来自用户上传或外部数据库，不自动生成模拟数据）


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
    similarity_threshold: float = float(os.getenv("CACHE_SIMILARITY_THRESHOLD", "0.95"))
    # 缓存 TTL（秒），默认 1 小时
    cache_ttl_seconds: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))


# ============================================================================
# LLM 配置 — 百炼统一入口（OpenAI 兼容模式）
# ============================================================================
# 百炼（阿里云 DashScope）OpenAI 兼容端点，作为各类 Key 槽位的默认兜底。
# 说明：qwen 与 deepseek 模型均上架百炼，可用同一把百炼 Key 调通，
# model_client 会按模型名前缀自动选择 Key 槽位，槽位为空时回退到此端点。
DASHSCOPE_COMPAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@dataclass
class LLMConfig:
    """大语言模型调用配置（百炼统一入口，OpenAI 兼容 API）"""
    # ---- 管理令牌（SHA256 哈希，为空时仅允许本地访问管理接口）----
    admin_token_hash: str = os.getenv("ADMIN_TOKEN", "")

    # ---- DeepSeek 统一入口（主，用户只需配这一个）----
    # 注意：DEEPSEEK_BASE_URL 默认指向百炼兼容端点（DashScope 上架的
    # deepseek-v4-* 系列）。若你用的是 DeepSeek 原生平台 Key，请把
    # DEEPSEEK_BASE_URL 改为 https://api.deepseek.com。
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "").strip()
    deepseek_base_url: str = os.getenv(
        "DEEPSEEK_BASE_URL",
        DASHSCOPE_COMPAT_URL,
    )

    # ---- Dashscope 备用 Key（可选）----
    dashscope_api_key: str = os.getenv("DASHSCOPE_API_KEY", "").strip()

    # ---- 旧 Key 字段：仅作降级回退保留，不推荐再单独维护 ----
    # resolved_api_key 在 DEEPSEEK/DASHSCOPE 均为空时，才会依次回退到
    # OPENAI_API_KEY / GLM_API_KEY，保证存量 .env 不迁移也能跑。
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base_url: str = os.getenv(
        "OPENAI_BASE_URL",
        DASHSCOPE_COMPAT_URL,
    )
    glm_api_key: str = os.getenv("GLM_API_KEY", "").strip()
    glm_base_url: str = os.getenv(
        "GLM_BASE_URL",
        DASHSCOPE_COMPAT_URL,
    )

    # 角色模型分级（可各自用 *_MODEL 环境变量覆盖；qwen 与 deepseek 均在百炼，
    #   model_client 按模型名前缀路由 Key/端点，见 model_client.resolve_for_model）：
    #   generator / critic —— 强推理，用 deepseek pro；router / reranker / 工具选择 —— 轻量，用 qwen flash
    router_model: str = os.getenv("ROUTER_MODEL", "qwen3.8-flash")
    generator_model: str = os.getenv("GENERATOR_MODEL", "deepseek-v4-pro-0813")
    critic_model: str = os.getenv("CRITIC_MODEL", "deepseek-v4-pro-0813")
    reranker_model: str = os.getenv("RERANKER_MODEL", "qwen3.8-flash")

    # ---- 统一解析结果（供 model_client.create_llm_client 使用）----

    @property
    def resolved_api_key(self) -> str:
        """主 Key 解析：DEEPSEEK → DASHSCOPE → OPENAI → GLM，返回第一个非空值。"""
        for key in (
            self.deepseek_api_key,
            self.dashscope_api_key,
            self.openai_api_key,
            self.glm_api_key,
        ):
            if key:
                return key
        return ""

    @property
    def resolved_provider(self) -> str:
        """当前生效的提供商标识（诊断/日志用），与 resolved_api_key 同序。"""
        if self.deepseek_api_key:
            return "deepseek"
        if self.dashscope_api_key:
            return "dashscope"
        if self.openai_api_key:
            return "openai"
        if self.glm_api_key:
            return "glm"
        return "none"

    @property
    def resolved_base_url(self) -> str:
        """与生效 Key 同源的端点解析（OpenAI 兼容）。

        规则：生效 Key 属于哪个提供商，就取哪个 base_url；全部未配置时
        退回百炼兼容端点（None 值不会出现，因为每个分支都有兜底）。
        """
        dashscope = DASHSCOPE_COMPAT_URL
        if self.deepseek_api_key:
            return (self.deepseek_base_url or dashscope).rstrip("/")
        if self.dashscope_api_key:
            return dashscope
        if self.openai_api_key:
            return (self.openai_base_url or dashscope).rstrip("/")
        if self.glm_api_key:
            return (self.glm_base_url or dashscope).rstrip("/")
        return dashscope

    # LLM 调用参数
    temperature: float = 0.1
    max_tokens: int = 2048


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
    # 是否启用 SQL 优化器
    enable_sql_optimizer: bool = os.getenv("ENABLE_SQL_OPTIMIZER", "true").lower() == "true"


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
    # 动态评估数据集的最大用例数（按当前连接库 schema 自适应生成，默认上限 24）
    max_cases: int = int(os.getenv("EVAL_MAX_CASES", "24"))


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
    agent: AgentConfig = field(default_factory=AgentConfig)
    file_processing: FileProcessingConfig = field(default_factory=FileProcessingConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)
    eval: EvaluationConfig = field(default_factory=EvaluationConfig)


# 全局单例配置
CONFIG = AppConfig()