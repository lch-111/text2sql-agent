"""
==============================================================================
FastAPI 应用入口 — 企业级智能数据分析 Agent
==============================================================================
"""

import logging
import time
from pathlib import Path
from contextlib import asynccontextmanager
from collections import defaultdict

from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from core.config import CONFIG
from api.routes import router
from api.auth import verify_admin_token

logger = logging.getLogger("app")

_RATE_LIMIT_MAP = defaultdict(list)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("[启动] 应用就绪")
    yield


app = FastAPI(
    title="Text-to-SQL Agent",
    description="企业级智能数据分析 Agent",
    version="3.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    # Rate limit for stream endpoint
    if request.url.path == "/api/chat/stream":
        ip_requests = _RATE_LIMIT_MAP[client_ip]
        _RATE_LIMIT_MAP[client_ip] = [t for t in ip_requests if now - t < 1]
        if len(_RATE_LIMIT_MAP[client_ip]) >= 3:
            return JSONResponse(status_code=429, content={"error": "请求过于频繁，请稍后重试"})
        _RATE_LIMIT_MAP[client_ip].append(now)

    # Admin auth
    if any(request.url.path.startswith(p) for p in ["/api/cache/clear", "/api/vector-store/"]):
        try:
            await verify_admin_token(request)
        except HTTPException as e:
            return JSONResponse(status_code=e.status_code, content={"error": e.detail})

    try:
        return await call_next(request)
    except Exception as e:
        logger.error(f"未捕获异常: {e}", exc_info=True)
        return JSONResponse(status_code=500, content={"error": "服务器内部错误"})


# API routes
app.include_router(router)


# Health check (MUST be before SPA catch-all)
@app.get("/health")
async def health():
    return {"status": "ok"}


# Serve React SPA
_react_dist = Path(__file__).parent / "frontend" / "dist"
if _react_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_react_dist / "assets")), name="react_assets")

    @app.get("/")
    @app.get("/chat")
    @app.get("/{full:path}")
    async def serve_spa(full: str = ""):
        # 优先返回 dist 中的静态文件（background.png, favicon 等）
        if full:
            file_path = _react_dist / full
            if file_path.exists() and file_path.is_file():
                return FileResponse(str(file_path))
        spa_index = _react_dist / "index.html"
        if spa_index.exists():
            return HTMLResponse(spa_index.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>React SPA not built yet</h1>")
else:
    # Fallback to Jinja2 templates
    from jinja2 import Environment, FileSystemLoader
    _template_env = Environment(loader=FileSystemLoader("templates"), auto_reload=False)

    def _url_for(name, path):
        return f"/static/{path}"
    _template_env.globals["url_for"] = _url_for

    @app.get("/", response_class=HTMLResponse)
    async def index():
        template = _template_env.get_template("index.html")
        return HTMLResponse(template.render())





# Static files (legacy)
_static_dir = Path(__file__).parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")
