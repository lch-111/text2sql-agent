"""
==============================================================================
Pydantic 模型定义 — API 请求/响应数据结构
==============================================================================
"""

import re
from pydantic import BaseModel, field_validator
from typing import Optional, List, Any, Dict


class ChatRequest(BaseModel):
    question: str
    history: Optional[List[Dict]] = None

    @field_validator("question")
    @classmethod
    def validate_question(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("问题不能为空")
        if len(v) > 500:
            raise ValueError("问题长度不能超过 500 字符")
        cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", v)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()


class ChatResponse(BaseModel):
    question: str
    sql: Optional[str] = None
    result: Optional[List[Dict[str, Any]]] = None
    columns: Optional[List[str]] = None
    error: Optional[str] = None
    cache_hit: bool = False
    cache_source: Optional[str] = None
    token_estimate: int = 0
    execution_time: float = 0.0
    retries: int = 0
    trace: Optional[Dict] = None


class DashboardControlRequest(BaseModel):
    region_filter: Optional[str] = None
    view_mode: Optional[str] = None
    chart_type: Optional[str] = None


class UploadResponse(BaseModel):
    filename: str
    file_type: str
    row_count: int
    summary: str
    sheets: Dict[str, Any]


class DbConnectionRequest(BaseModel):
    db_type: str = "mysql"
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    user: Optional[str] = None
    password: str = ""

    @field_validator("host", "database", "user")
    @classmethod
    def validate_db_param(cls, v: Optional[str]) -> Optional[str]:
        if v and any(c in v for c in [";", "--", "DROP", "drop"]):
            raise ValueError(f"参数包含危险字符: {v[:20]}")
        return v


class DbConnectionTestResult(BaseModel):
    success: bool
    message: str
    version: Optional[str] = None
    latency_ms: Optional[float] = None


class DbConnectionStatus(BaseModel):
    connected: bool
    db_type: str = ""
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    user: Optional[str] = None
    active_tables: int = 0
    tables: List[str] = []
