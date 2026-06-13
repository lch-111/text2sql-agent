"""
==============================================================================
API 认证与安全中间件
==============================================================================
"""

import hashlib
import logging
from functools import wraps
from fastapi import Request, HTTPException, Depends
from fastapi.responses import JSONResponse

from core.config import CONFIG

logger = logging.getLogger("auth")

# 需要认证的管理接口路径前缀
ADMIN_PATHS = ["/api/cache/clear", "/api/vector-store/", "/api/eval/"]


def is_admin_path(path: str) -> bool:
    """判断是否为管理接口"""
    for prefix in ADMIN_PATHS:
        if path.startswith(prefix):
            return True
    return False


async def verify_admin_token(request: Request):
    """验证管理接口的认证令牌"""
    path = request.url.path
    if not is_admin_path(path):
        return True

    token_config = CONFIG.llm.admin_token if hasattr(CONFIG.llm, 'admin_token') else ""

    if not token_config:
        # 未配置令牌，仅允许本地访问
        host = request.client.host if request.client else ""
        if host in ("127.0.0.1", "::1", "localhost"):
            return True
        raise HTTPException(status_code=403, detail="未配置 ADMIN_TOKEN，仅允许本地访问")

    # Bearer 令牌验证
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if token_hash == token_config:
            return True

    raise HTTPException(status_code=401, detail="未授权：缺少或无效的管理令牌")


async def admin_required(request: Request):
    """依赖注入：要求管理员权限"""
    await verify_admin_token(request)
    return True
