"""
==============================================================================
FastAPI 应用入口 — 企业级智能数据分析 Agent
==============================================================================
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from jinja2 import Environment, FileSystemLoader

from api.routes import router

logger = logging.getLogger("app")

# Jinja2 模板（直接使用 Jinja2 而非 Starlette 的 Jinja2Templates）
_template_env = Environment(
    loader=FileSystemLoader("templates"),
    auto_reload=False,
    cache_size=0,
)

# 添加 url_for 辅助函数
def _url_for(name, path):
    """在模板中生成静态文件 URL"""
    return f"/static/{path}"


_template_env.globals["url_for"] = _url_for


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库和向量索引"""
    logger.info("[启动] 应用就绪（跳过数据初始化，由用户自行连接数据库或上传文件）")
    yield


app = FastAPI(
    title="Text-to-SQL Agent",
    description="企业级智能数据分析 Agent",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS 中间件（允许跨域请求）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# API 路由
app.include_router(router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """主页面"""
    template = _template_env.get_template("index.html")
    html = template.render()
    return HTMLResponse(html)


@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok"}
