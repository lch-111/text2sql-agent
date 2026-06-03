"""
==============================================================================
Pydantic 模型定义 — API 请求/响应数据结构
==============================================================================
"""

from pydantic import BaseModel
from typing import Optional, List, Any, Dict


class ChatRequest(BaseModel):
    question: str


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


# ============================================================================
# 数据库连接
# ============================================================================

class DbConnectionRequest(BaseModel):
    """数据库连接请求"""
    db_type: str = "mysql"  # mysql | postgres | sqlite
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    user: Optional[str] = None
    password: str = ""


class DbConnectionTestResult(BaseModel):
    """数据库连接测试结果"""
    success: bool
    message: str
    version: Optional[str] = None
    latency_ms: Optional[float] = None


class DbConnectionStatus(BaseModel):
    """当前数据库连接状态"""
    connected: bool
    db_type: str = ""
    host: Optional[str] = None
    port: Optional[int] = None
    database: Optional[str] = None
    user: Optional[str] = None
    active_tables: int = 0
    tables: List[str] = []
