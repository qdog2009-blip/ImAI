"""
WebSocket 连接管理器 — 纯 asyncio，零阻塞 I/O。

三大核心机制:
  1. 定时 Ping/Pong 心跳 — protobuf Envelope，超时主动断连
  2. Client_Msg_ID 防重防抖 — 基于 BaseCacheProvider 幂等校验
  3. 多设备会话挤占 — Cache 持久化会话注册表，跨节点/重启可感知
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from app.core.config import settings
from app.core.protocol import build_kick, build_ping
from app.services.cache.base import BaseCacheProvider

logger = logging.getLogger(__name__)

# ---- 缓存键前缀 & TTL ----
_DEDUP_PREFIX = "dedup:msg:"
_DEDUP_TTL = 300                          # 5 分钟内同 client_msg_id 视为重复
_SESSION_PREFIX = "ws:sessions:"          # 多设备会话注册表
_SESSION_TTL_MULTIPLIER = 3              # session TTL = heartbeat * 3


# ------------------------------------------------------------------ #
#  单条连接数据
# ------------------------------------------------------------------ #

@dataclass
class Connection:
    ws: WebSocket
    user_id: str
    device_id: str
    connected_at: float = field(default_factory=time.time)
    last_pong_at: float = field(default_factory=time.time)
    _heartbeat_task: Optional[asyncio.Task] = field(
        default=None, repr=False, compare=False,
    )

    def record_pong(self) -> None:
        """API 层收到 Pong Envelope 后调用，刷新存活时间戳。"""
        self.last_pong_at = time.time()

    @property
    def is_alive(self) -> bool:
        return self.ws.client_state == WebSocketState.CONNECTED


# ------------------------------------------------------------------ #
#  连接管理器
# ------------------------------------------------------------------ #

class ConnectionManager:

    def __init__(self, cache: BaseCacheProvider) -> None:
        self._conns: dict[str, list[Connection]] = {}
        self._cache = cache
        self._lock = asyncio.Lock()
        self._ping_payload: bytes = build_ping()
        self._session_ttl: int = settings.WS_HEARTBEAT_INTERVAL * _SESSION_TTL_MULTIPLIER

    # ================================================================ #
    #  1. 生命周期
    # ================================================================ #

    async def connect(
        self,
        ws: WebSocket,
        user_id: str,
        device_id: str,
    ) -> Connection:
        """
        接受握手 → Cache 注册会话 → 超限挤占 → 本地登记 → 启动心跳。
        """
        await ws.accept()
        conn = Connection(ws=ws, user_id=user_id, device_id=device_id)

        # ---- Cache 会话注册 & 挤占判定 ----
        devices_to_kick = await self._register_session(user_id, device_id)

        async with self._lock:
            user_conns = self._conns.setdefault(user_id, [])

            # 踢掉 Cache 判定需要挤占的设备
            for kick_dev in devices_to_kick:
                victim = next(
                    (c for c in user_conns if c.device_id == kick_dev), None,
                )
                if victim:
                    kick_data = build_kick(
                        reason="session_replaced", device_id=device_id,
                    )
                    await self._safe_send(victim, kick_data)
                    user_conns.remove(victim)
                    await self._force_close(victim, code=4001, reason="session_replaced")

            user_conns.append(conn)

        # 启动心跳协程
        conn._heartbeat_task = asyncio.create_task(self._heartbeat_loop(conn))

        logger.info(
            "ws.connect  user=%s device=%s online=%d",
            user_id, device_id, self.online_count,
        )
        return conn

    async def disconnect(self, conn: Connection) -> None:
        """取消心跳 → Cache 注销会话 → 本地移除。"""
        if conn._heartbeat_task and not conn._heartbeat_task.done():
            conn._heartbeat_task.cancel()

        await self._unregister_session(conn.user_id, conn.device_id)

        async with self._lock:
            user_conns = self._conns.get(conn.user_id, [])
            try:
                user_conns.remove(conn)
            except ValueError:
                pass
            if not user_conns:
                self._conns.pop(conn.user_id, None)

        logger.info(
            "ws.disconnect  user=%s device=%s online=%d",
            conn.user_id, conn.device_id, self.online_count,
        )

    async def close_all(self) -> None:
        """应用关闭，优雅断开全部连接并清理 Cache。"""
        async with self._lock:
            all_conns = [c for conns in self._conns.values() for c in conns]
            self._conns.clear()

        for conn in all_conns:
            await self._unregister_session(conn.user_id, conn.device_id)
            await self._force_close(conn, code=1001, reason="server_shutdown")

    # ================================================================ #
    #  2. 消息投递
    # ================================================================ #

    async def send_to_user(self, user_id: str, data: bytes) -> None:
        for conn in self._conns.get(user_id, []):
            await self._safe_send(conn, data)

    async def send_to_connection(self, conn: Connection, data: bytes) -> None:
        await self._safe_send(conn, data)

    async def broadcast(
        self,
        data: bytes,
        *,
        exclude_user: Optional[str] = None,
    ) -> None:
        for user_id, conns in self._conns.items():
            if user_id == exclude_user:
                continue
            for conn in conns:
                await self._safe_send(conn, data)

    # ================================================================ #
    #  3. Client_Msg_ID 防重防抖
    # ================================================================ #

    async def is_duplicate(self, client_msg_id: str) -> bool:
        """
        幂等校验 — 基于 Cache TTL:
          首次: 写入 Cache → 返回 False
          重复: Cache 命中 → 返回 True
        """
        key = f"{_DEDUP_PREFIX}{client_msg_id}"
        if await self._cache.exists(key):
            return True
        await self._cache.set(key, b"1", ttl=_DEDUP_TTL)
        return False

    # ================================================================ #
    #  4. 在线状态查询
    # ================================================================ #

    @property
    def online_count(self) -> int:
        return sum(len(c) for c in self._conns.values())

    def is_online(self, user_id: str) -> bool:
        return bool(self._conns.get(user_id))

    def get_online_user_ids(self) -> list[str]:
        return list(self._conns.keys())

    def get_user_connections(self, user_id: str) -> list[Connection]:
        return list(self._conns.get(user_id, []))

    # ================================================================ #
    #  内部 — Ping/Pong 心跳
    # ================================================================ #

    async def _heartbeat_loop(self, conn: Connection) -> None:
        """
        定时发送 protobuf Ping Envelope:
          → 客户端必须回复 Pong Envelope
          → 超过 2×interval 未收到 Pong → 判定死亡 → 主动断连
          → 每轮刷新 Cache 会话 TTL (续期)
        """
        interval = settings.WS_HEARTBEAT_INTERVAL
        timeout = interval * 2

        try:
            while conn.is_alive:
                await asyncio.sleep(interval)

                # ① 检查上一轮 Pong 是否按时到达
                if time.time() - conn.last_pong_at > timeout:
                    logger.warning(
                        "ws.heartbeat_timeout  user=%s device=%s "
                        "last_pong=%.1fs_ago",
                        conn.user_id,
                        conn.device_id,
                        time.time() - conn.last_pong_at,
                    )
                    await self._force_close(
                        conn, code=4002, reason="heartbeat_timeout",
                    )
                    return

                # ② 发送 Ping Envelope
                if conn.is_alive:
                    await self._safe_send(conn, self._ping_payload)

                # ③ 续期 Cache 会话 TTL
                await self._refresh_session(conn.user_id, conn.device_id)

        except asyncio.CancelledError:
            pass

    # ================================================================ #
    #  内部 — Cache 会话注册表 (多设备挤占)
    # ================================================================ #
    #
    #  Cache 键:  ws:sessions:{user_id}
    #  Cache 值:  JSON bytes → [{"device_id": "...", "ts": 1234567890.0}, ...]
    #  TTL:       heartbeat_interval × 3 (心跳每轮续期)
    #

    async def _register_session(
        self, user_id: str, device_id: str,
    ) -> list[str]:
        """
        注册新会话到 Cache，返回需要踢掉的 device_id 列表。
        """
        key = f"{_SESSION_PREFIX}{user_id}"
        sessions = await self._load_sessions(key)

        # 过滤掉同设备的旧条目 (重连场景)
        sessions = [s for s in sessions if s["device_id"] != device_id]

        # 超限: 按连接时间升序，踢掉最早的
        to_kick: list[str] = []
        while len(sessions) >= settings.WS_MAX_CONNECTIONS_PER_USER:
            oldest = sessions.pop(0)
            to_kick.append(oldest["device_id"])

        # 追加新会话
        sessions.append({"device_id": device_id, "ts": time.time()})
        await self._save_sessions(key, sessions)

        if to_kick:
            logger.info(
                "ws.session_evict  user=%s kicked=%s by=%s",
                user_id, to_kick, device_id,
            )
        return to_kick

    async def _unregister_session(self, user_id: str, device_id: str) -> None:
        """从 Cache 移除指定设备会话。"""
        key = f"{_SESSION_PREFIX}{user_id}"
        sessions = await self._load_sessions(key)
        sessions = [s for s in sessions if s["device_id"] != device_id]

        if sessions:
            await self._save_sessions(key, sessions)
        else:
            await self._cache.delete(key)

    async def _refresh_session(self, user_id: str, device_id: str) -> None:
        """心跳续期: 刷新该用户会话注册表的 TTL。"""
        key = f"{_SESSION_PREFIX}{user_id}"
        if await self._cache.exists(key):
            await self._cache.expire(key, self._session_ttl)

    async def _load_sessions(self, key: str) -> list[dict]:
        raw = await self._cache.get(key)
        if not raw:
            return []
        return json.loads(raw)

    async def _save_sessions(self, key: str, sessions: list[dict]) -> None:
        await self._cache.set(
            key, json.dumps(sessions).encode(), ttl=self._session_ttl,
        )

    # ================================================================ #
    #  内部 — 发送 / 关闭
    # ================================================================ #

    async def _safe_send(self, conn: Connection, data: bytes) -> None:
        try:
            if conn.is_alive:
                await conn.ws.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError, ConnectionError):
            logger.debug(
                "ws.send_failed  user=%s device=%s",
                conn.user_id, conn.device_id,
            )

    async def _force_close(
        self,
        conn: Connection,
        *,
        code: int = 1000,
        reason: str = "",
    ) -> None:
        if conn._heartbeat_task and not conn._heartbeat_task.done():
            conn._heartbeat_task.cancel()
        try:
            await conn.ws.close(code=code, reason=reason)
        except Exception:
            pass
