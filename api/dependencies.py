"""
==============================================================================
FastAPI 依赖注入 — 提供 agent / db / cache 单例
==============================================================================
"""

from functools import lru_cache
from database import get_db as _get_db
from cache import get_cache as _get_cache


def get_agent():
    from agent import TextToSQLAgent
    return TextToSQLAgent()


def get_db():
    return _get_db()


def get_cache():
    return _get_cache()
