"""
缓存工厂 — 根据 settings.CACHE_TYPE 创建对应实现。

用法:
    from app.services.cache import create_cache_provider
    cache = create_cache_provider()     # 返回 BaseCacheProvider 实例
"""

from __future__ import annotations

from app.core.config import settings
from app.services.cache.base import BaseCacheProvider


def create_cache_provider() -> BaseCacheProvider:
    """工厂方法: 根据 CACHE_TYPE 环境变量实例化缓存策略。"""
    if settings.CACHE_TYPE == "redis":
        from app.services.cache.redis import RedisCacheProvider
        return RedisCacheProvider(settings.redis_url)

    from app.services.cache.file import FileCacheProvider
    return FileCacheProvider(settings.FILE_CACHE_DIR)
