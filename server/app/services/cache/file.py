"""
FileCacheProvider — 基于 aiofiles 的本地文件缓存实现。

适用于轻量部署 / 开发环境 (CACHE_TYPE=file)。
每个缓存键映射为一个独立文件，首行存储过期时间戳。
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Optional

import aiofiles
import aiofiles.os

from app.services.cache.base import BaseCacheProvider

# 文件格式:
#   第 1 行: 过期时间戳 (Unix 秒, "0" 代表永不过期)
#   第 2 行起: 原始 value (base64 编码)
_NO_EXPIRY = b"0"
_SEP = b"\n"


class FileCacheProvider(BaseCacheProvider):

    def __init__(self, cache_dir: str) -> None:
        self._dir = Path(cache_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---- 接口实现 ----

    async def get(self, key: str) -> Optional[bytes]:
        path = self._key_path(key)
        if not path.exists():
            return None

        async with aiofiles.open(path, "rb") as f:
            raw = await f.read()

        expire_line, _, value = raw.partition(_SEP)
        if self._is_expired(expire_line):
            await self.delete(key)
            return None
        return value

    async def set(self, key: str, value: bytes, ttl: Optional[int] = None) -> None:
        expire_ts = _NO_EXPIRY if ttl is None else str(int(time.time()) + ttl).encode()
        path = self._key_path(key)
        async with aiofiles.open(path, "wb") as f:
            await f.write(expire_ts + _SEP + value)

    async def delete(self, key: str) -> None:
        path = self._key_path(key)
        try:
            await aiofiles.os.remove(path)
        except FileNotFoundError:
            pass

    async def exists(self, key: str) -> bool:
        return (await self.get(key)) is not None

    async def expire(self, key: str, ttl: int) -> None:
        value = await self.get(key)
        if value is not None:
            await self.set(key, value, ttl)

    async def close(self) -> None:
        pass  # 文件缓存无需释放连接

    # ---- 内部方法 ----

    def _key_path(self, key: str) -> Path:
        """将缓存键哈希为安全文件名。"""
        digest = hashlib.sha256(key.encode()).hexdigest()
        return self._dir / digest

    @staticmethod
    def _is_expired(expire_line: bytes) -> bool:
        if expire_line == _NO_EXPIRY:
            return False
        try:
            return time.time() > float(expire_line)
        except ValueError:
            return True
