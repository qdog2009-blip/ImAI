"""
BaseCacheProvider — 缓存策略抽象接口。

所有缓存实现 (Redis / File) 必须继承此基类并实现全部抽象方法。
上层业务只依赖此接口，不感知底层实现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class BaseCacheProvider(ABC):

    @abstractmethod
    async def get(self, key: str) -> Optional[bytes]:
        """读取缓存值，不存在或已过期返回 None。"""

    @abstractmethod
    async def set(self, key: str, value: bytes, ttl: Optional[int] = None) -> None:
        """写入缓存值。ttl 单位为秒，None 表示永不过期。"""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """删除指定缓存键。键不存在时静默忽略。"""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """判断键是否存在且未过期。"""

    @abstractmethod
    async def expire(self, key: str, ttl: int) -> None:
        """为已有键设置/更新过期时间 (秒)。"""

    @abstractmethod
    async def close(self) -> None:
        """释放底层连接资源。应用关闭时调用。"""
