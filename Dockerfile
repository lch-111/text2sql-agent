# =============================================================================
# Dockerfile — 企业级智能数据分析 Agent (FastAPI + ECharts)
# =============================================================================
# 构建:
#   docker build -t text2sql-agent .
# 运行:
#   docker run -p 8000:8000 text2sql-agent
# =============================================================================

FROM python:3.12-slim

WORKDIR /app

# ---- 安装 Python 依赖 ----
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---- 复制应用代码 ----
COPY . .

# ---- 下载 ECharts (优先使用国内 CDN 镜像) ----
RUN pip install requests --quiet && python -c "import requests; r = requests.get('https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js', timeout=30); open('static/js/echarts.min.js','wb').write(r.content)" 2>/dev/null || \
    python -c "import requests; r = requests.get('https://registry.npmmirror.com/echarts/5.5.0/files/dist/echarts.min.js', timeout=30); open('static/js/echarts.min.js','wb').write(r.content)" 2>/dev/null || \
    echo "[WARN] ECharts download failed, will use CDN at runtime"

# ---- 创建数据目录 ----
RUN mkdir -p data logs eval_results

# ---- 暴露 FastAPI 端口 ----
EXPOSE 8000

# ---- 健康检查 ----
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1
