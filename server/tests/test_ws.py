"""
WebSocket 连接管理器集成测试 — websockets 客户端 × protobuf 二进制帧。

测试场景:
  1. 建立连接 + 验证握手成功 + ConnectionManager 在线跟踪
  2. 心跳: 等待服务端 Ping Envelope → 发送 Pong → 验证连接存活
  3. 多设备挤占: 超出 MAX_CONNECTIONS_PER_USER → 最早设备收到 KickNotice
     → Cache 会话注册表仅保留最新设备

运行方式 (本地 SQLite):
  cd server && PYTHONPATH=. DB_TYPE=sqlite CACHE_TYPE=file APP_ENV=test \\
    SERVER_SECRET_KEY=test_key JWT_SECRET_KEY=test_jwt_secret_32byteslong! \\
    WS_HEARTBEAT_INTERVAL=2 WS_MAX_CONNECTIONS_PER_USER=2 \\
    pytest tests/test_ws.py -v
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time as _time

import pytest
import uvicorn
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from app.core.config import settings
from app.core.protocol import build_pong, parse_envelope
from app.generated.chat_pb2 import EnvelopeType


# ------------------------------------------------------------------ #
#  辅助
# ------------------------------------------------------------------ #

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ws_uri(base: str, user_id: str, device_id: str) -> str:
    return f"{base}/ws?user_id={user_id}&device_id={device_id}"


async def _recv_envelope(ws, *, timeout: float = 10):
    """读取一个 protobuf Envelope，超时抛出 TimeoutError。"""
    data = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return parse_envelope(data)


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def ws_base_url():
    """
    在后台线程启动真实 uvicorn 服务器 (module 级，只启动一次)。
    yield ws://127.0.0.1:{port}，测试结束后优雅关闭。

    使用独立线程确保服务端事件循环与 pytest-asyncio 的
    per-function 事件循环互不干扰，websockets 客户端通过
    TCP 跨线程通信。
    """
    from main import app

    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        ws="websockets",
        ws_ping_interval=None,     # 禁用 websockets 库级心跳
        ws_ping_timeout=None,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # 等待服务器就绪 (lifespan 完成 + 监听端口)
    for _ in range(100):
        _time.sleep(0.1)
        if server.started:
            break
    else:
        raise RuntimeError("uvicorn test server failed to start within 10s")

    yield f"ws://127.0.0.1:{port}"

    server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture(autouse=True)
async def _settle():
    """每个用例结束后等待服务端处理完断连回调。"""
    yield
    await asyncio.sleep(0.5)


# ================================================================== #
#  1. 建立连接
# ================================================================== #


class TestConnect:

    async def test_ws_handshake_succeeds(self, ws_base_url: str):
        """WebSocket 握手成功，能收到服务端的第一次心跳 Ping。"""
        async with ws_connect(
            _ws_uri(ws_base_url, "conn_u1", "d1"),
            ping_interval=None,
        ) as ws:
            env = await _recv_envelope(
                ws, timeout=settings.WS_HEARTBEAT_INTERVAL + 3,
            )
            assert env.type == EnvelopeType.PING

    async def test_manager_tracks_online_user(self, ws_base_url: str):
        """连接后 ConnectionManager.is_online() 返回 True，断开后为 False。"""
        from main import app

        user_id = "online_u1"

        async with ws_connect(
            _ws_uri(ws_base_url, user_id, "d1"),
            ping_interval=None,
        ) as ws:
            await asyncio.sleep(0.3)
            assert app.state.ws_manager.is_online(user_id)

        # 关闭后等待服务端 disconnect 回调
        await asyncio.sleep(0.5)
        assert not app.state.ws_manager.is_online(user_id)


# ================================================================== #
#  2. 心跳 Ping / Pong
# ================================================================== #


class TestHeartbeat:

    async def test_server_sends_ping_client_replies_pong(self, ws_base_url: str):
        """
        完整心跳周期:
          ① 服务端发送 protobuf Ping Envelope
          ② 客户端回复 Pong Envelope (protobuf 序列化)
          ③ 服务端再次发送 Ping — 证明 Pong 被接受、连接存活
        """
        interval = settings.WS_HEARTBEAT_INTERVAL
        timeout = interval + 3

        async with ws_connect(
            _ws_uri(ws_base_url, "hb_u1", "d1"),
            ping_interval=None,
        ) as ws:
            # ① 接收第一次 Ping
            env1 = await _recv_envelope(ws, timeout=timeout)
            assert env1.type == EnvelopeType.PING

            # ② 回复 Pong (protobuf 格式)
            await ws.send(build_pong())

            # ③ 接收第二次 Ping — 连接仍然存活
            env2 = await _recv_envelope(ws, timeout=timeout)
            assert env2.type == EnvelopeType.PING

    async def test_no_pong_causes_timeout_disconnect(self, ws_base_url: str):
        """
        不回复 Pong → 超过 2×interval 后服务端主动断连 (code=4002)。
        """
        interval = settings.WS_HEARTBEAT_INTERVAL
        max_wait = interval * 4 + 3

        ws = await ws_connect(
            _ws_uri(ws_base_url, "hb_dead_u1", "d1"),
            ping_interval=None,
        )

        try:
            # 收到 Ping 但故意不回 Pong
            env = await _recv_envelope(ws, timeout=interval + 3)
            assert env.type == EnvelopeType.PING

            # 持续读取直到服务端因心跳超时关闭连接
            with pytest.raises(
                (ConnectionClosedError, ConnectionClosedOK),
            ):
                while True:
                    await asyncio.wait_for(ws.recv(), timeout=max_wait)
        finally:
            await ws.close()


# ================================================================== #
#  3. 多设备挤占 (Cache 驱动)
# ================================================================== #


class TestSessionEviction:

    async def test_exceed_max_kicks_oldest_device(self, ws_base_url: str):
        """
        同一 user_id 连接超过 MAX_CONNECTIONS_PER_USER:
          ① 连接 N 个设备 (N = max)
          ② 连接第 N+1 个 → Cache 触发挤占
          ③ 第 1 个设备收到 KickNotice (protobuf)
          ④ 第 1 个设备连接被关闭 (code=4001)
          ⑤ Cache 会话注册表仅保留最新 N 个设备 ID
        """
        from main import app

        max_conn = settings.WS_MAX_CONNECTIONS_PER_USER
        user_id = "evict_u1"

        # ① 连接 max 个设备
        connections = []
        for i in range(max_conn):
            ws = await ws_connect(
                _ws_uri(ws_base_url, user_id, f"dev_{i}"),
                ping_interval=None,
            )
            connections.append(ws)
            await asyncio.sleep(0.15)  # 保证注册时间戳有序

        oldest_ws = connections[0]

        # ② 连接第 max+1 个设备 → 触发挤占 dev_0
        extra_ws = await ws_connect(
            _ws_uri(ws_base_url, user_id, f"dev_{max_conn}"),
            ping_interval=None,
        )

        try:
            # ③ 最早的设备收到 KickNotice
            env = await _recv_envelope(oldest_ws, timeout=3)
            assert env.type == EnvelopeType.KICK_NOTICE
            assert env.kick_notice.reason == "session_replaced"

            # ④ oldest_ws 被服务端关闭
            with pytest.raises(
                (ConnectionClosedError, ConnectionClosedOK),
            ):
                await asyncio.wait_for(oldest_ws.recv(), timeout=2)

            # ⑤ 验证 Cache 会话注册表
            cache = app.state.cache
            raw = await cache.get(f"ws:sessions:{user_id}")
            assert raw is not None
            sessions = json.loads(raw)
            registered = {s["device_id"] for s in sessions}

            expected = {f"dev_{i}" for i in range(1, max_conn + 1)}
            assert registered == expected
            assert "dev_0" not in registered

        finally:
            for ws in connections[1:]:
                await ws.close()
            await extra_ws.close()

    async def test_kick_notice_carries_new_device_id(self, ws_base_url: str):
        """KickNotice.device_id 标识 '谁' 把你挤下线。"""
        max_conn = settings.WS_MAX_CONNECTIONS_PER_USER
        user_id = "kick_who_u1"

        connections = []
        for i in range(max_conn):
            ws = await ws_connect(
                _ws_uri(ws_base_url, user_id, f"dev_{i}"),
                ping_interval=None,
            )
            connections.append(ws)
            await asyncio.sleep(0.15)

        new_device = "intruder_x"
        extra_ws = await ws_connect(
            _ws_uri(ws_base_url, user_id, new_device),
            ping_interval=None,
        )

        try:
            env = await _recv_envelope(connections[0], timeout=3)
            assert env.type == EnvelopeType.KICK_NOTICE
            assert env.kick_notice.device_id == new_device
        finally:
            for ws in connections[1:]:
                await ws.close()
            await extra_ws.close()

    async def test_same_device_reconnect_no_kick(self, ws_base_url: str):
        """同 device_id 重连: Cache 只保留一个条目，不触发多余挤占。"""
        from main import app

        user_id = "recon_u1"
        device_id = "same_dev"

        ws1 = await ws_connect(
            _ws_uri(ws_base_url, user_id, device_id),
            ping_interval=None,
        )
        await asyncio.sleep(0.3)

        # 同 device_id 再次连接 (模拟网络闪断后重连)
        ws2 = await ws_connect(
            _ws_uri(ws_base_url, user_id, device_id),
            ping_interval=None,
        )
        await asyncio.sleep(0.3)

        try:
            cache = app.state.cache
            raw = await cache.get(f"ws:sessions:{user_id}")
            assert raw is not None
            sessions = json.loads(raw)

            # 同一 device_id 在 Cache 中只保留 1 条
            same_entries = [
                s for s in sessions if s["device_id"] == device_id
            ]
            assert len(same_entries) == 1
        finally:
            await ws1.close()
            await ws2.close()
