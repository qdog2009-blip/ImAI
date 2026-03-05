"""
RedisCacheProvider — 基于 redis.asyncio 的高并发缓存实现。

适用于生产环境 / 高并发场景 (CACHE_TYPE=redis)。
"""

from __future__ import annotations

from typing import Optional

from redis.asyncio import Redis

from app.services.cache.base import BaseCacheProvider


class RedisCacheProvider(BaseCacheProvider):

    def __init__(self, redis_url: str) -> None:
        self._redis: Redis = Redis.from_url(
            redis_url,
            decode_responses=False,   # 保持 bytes 语义
        )

    async def get(self, key: str) -> Optional[bytes]:
        return await self._redis.get(key)

    async def set(self, key: str, value: bytes, ttl: Optional[int] = None) -> None:
        if ttl is not None:
            await self._redis.set(key, value, ex=ttl)
        else:
            await self._redis.set(key, value)

    async def delete(self, key: str) -> None:
        await self._redis.delete(key)

    async def exists(self, key: str) -> bool:
        return (await self._redis.exists(key)) > 0

    async def expire(self, key: str, ttl: int) -> None:
        await self._redis.expire(key, ttl)

    async def close(self) -> None:
        await self._redis.aclose()
