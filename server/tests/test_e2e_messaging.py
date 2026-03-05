"""
E2E 集成测试 — 两个并发 WebSocket 客户端 protobuf 消息收发。

测试场景:
  1. Client A 发送文本消息 → Client B 在 100ms 内收到
  2. Client A 收到 MessageAck(SENT) 携带正确 server_msg_id
  3. Client B 收到的消息字段与 Client A 发送的完全一致
  4. 连续发送 5 条消息 → 全部按序到达
  5. 双向收发: A→B 和 B→A 均正常

运行方式 (本地 SQLite):
  cd server && PYTHONPATH=. DB_TYPE=sqlite CACHE_TYPE=file APP_ENV=test \
    SERVER_SECRET_KEY=test_key JWT_SECRET_KEY=test_jwt_secret_32byteslong! \
    pytest tests/test_e2e_messaging.py -v
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time as _time
import uuid

import pytest
import uvicorn
from websockets.asyncio.client import connect as ws_connect

from app.core.protocol import build_pong, parse_envelope
from app.generated.chat_pb2 import (
    AckStatus,
    ChannelType,
    ChatMessage,
    ContentType,
    Envelope,
    EnvelopeType,
    TextContent,
)


# ------------------------------------------------------------------ #
#  辅助
# ------------------------------------------------------------------ #

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _ws_uri(base: str, user_id: str, device_id: str = "d1") -> str:
    return f"{base}/ws?user_id={user_id}&device_id={device_id}"


async def _recv_envelope(ws, *, timeout: float = 10) -> Envelope:
    """读取一个 protobuf Envelope，超时抛出 TimeoutError。"""
    data = await asyncio.wait_for(ws.recv(), timeout=timeout)
    return parse_envelope(data)


async def _recv_skip_ping(ws, *, timeout: float = 10) -> Envelope:
    """接收下一个非 Ping 信封，自动回复 Pong 以保持连接存活。"""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError(
                f"no non-PING envelope within {timeout}s"
            )
        env = await _recv_envelope(ws, timeout=remaining)
        if env.type == EnvelopeType.PING:
            await ws.send(build_pong())
            continue
        return env


def _build_text_envelope(
    client_msg_id: str,
    sender_id: str,
    receiver_id: str,
    text: str,
) -> bytes:
    """构造一个 TEXT 类型 ChatMessage Envelope 的 protobuf 二进制。"""
    msg = ChatMessage(
        client_msg_id=client_msg_id,
        sender_id=sender_id,
        receiver_id=receiver_id,
        channel=ChannelType.PRIVATE,
        content_type=ContentType.TEXT,
        text=TextContent(text=text),
    )
    return Envelope(
        type=EnvelopeType.CHAT_MESSAGE,
        chat_message=msg,
    ).SerializeToString()


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

@pytest.fixture(scope="module")
def ws_base_url():
    """
    后台线程启动 uvicorn (module 级)，测试结束后优雅关闭。
    """
    from main import app

    port = _free_port()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        ws="websockets",
        ws_ping_interval=None,
        ws_ping_timeout=None,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

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
#  E2E 消息收发测试
# ================================================================== #


class TestE2EMessaging:

    # -------------------------------------------------------------- #
    #  1. 100ms 内送达
    # -------------------------------------------------------------- #

    async def test_message_delivered_within_100ms(self, ws_base_url: str):
        """
        Client A 发送一条 TEXT 消息给 Client B，
        Client B 在 100ms 内收到且字段正确。
        """
        cid = uuid.uuid4().hex
        text = "你好，这是一条端到端测试消息！"

        async with (
            ws_connect(
                _ws_uri(ws_base_url, "alice", "d1"), ping_interval=None,
            ) as ws_a,
            ws_connect(
                _ws_uri(ws_base_url, "bob", "d1"), ping_interval=None,
            ) as ws_b,
        ):
            # 等待双方连接注册完成
            await asyncio.sleep(0.3)

            t0 = asyncio.get_event_loop().time()
            await ws_a.send(_build_text_envelope(
                client_msg_id=cid,
                sender_id="alice",
                receiver_id="bob",
                text=text,
            ))

            # Client B 接收 — 限时 100ms
            env = await _recv_skip_ping(ws_b, timeout=0.1)
            latency_ms = (asyncio.get_event_loop().time() - t0) * 1000

            assert env.type == EnvelopeType.CHAT_MESSAGE
            msg = env.chat_message
            assert msg.client_msg_id == cid
            assert msg.sender_id == "alice"
            assert msg.receiver_id == "bob"
            assert msg.content_type == ContentType.TEXT
            assert msg.text.text == text
            assert msg.server_msg_id > 0, "服务端应分配 server_msg_id"
            assert msg.created_at > 0, "服务端应填充 created_at"
            assert latency_ms < 100, (
                f"消息投递延迟 {latency_ms:.1f}ms 超过 100ms 阈值"
            )

    # -------------------------------------------------------------- #
    #  2. 发送方收到 MessageAck
    # -------------------------------------------------------------- #

    async def test_sender_receives_ack_with_server_msg_id(
        self, ws_base_url: str,
    ):
        """
        Client A 发送消息后收到 MessageAck(SENT)，
        ACK 携带正确的 client_msg_id 和非零 server_msg_id。
        """
        cid = uuid.uuid4().hex

        async with (
            ws_connect(
                _ws_uri(ws_base_url, "ack_alice"), ping_interval=None,
            ) as ws_a,
            ws_connect(
                _ws_uri(ws_base_url, "ack_bob"), ping_interval=None,
            ) as ws_b,
        ):
            await asyncio.sleep(0.3)

            await ws_a.send(_build_text_envelope(
                client_msg_id=cid,
                sender_id="ack_alice",
                receiver_id="ack_bob",
                text="ACK 测试",
            ))

            env = await _recv_skip_ping(ws_a, timeout=1)
            assert env.type == EnvelopeType.MESSAGE_ACK
            ack = env.message_ack
            assert ack.client_msg_id == cid
            assert ack.status == AckStatus.SENT
            assert ack.server_msg_id > 0

    # -------------------------------------------------------------- #
    #  3. 接收方字段完整性
    # -------------------------------------------------------------- #

    async def test_receiver_gets_all_fields_intact(self, ws_base_url: str):
        """Client B 收到的 ChatMessage 字段与 Client A 发送的一致。"""
        cid = uuid.uuid4().hex
        text_body = "字段一致性验证 🔍"

        async with (
            ws_connect(
                _ws_uri(ws_base_url, "field_alice"), ping_interval=None,
            ) as ws_a,
            ws_connect(
                _ws_uri(ws_base_url, "field_bob"), ping_interval=None,
            ) as ws_b,
        ):
            await asyncio.sleep(0.3)

            await ws_a.send(_build_text_envelope(
                client_msg_id=cid,
                sender_id="field_alice",
                receiver_id="field_bob",
                text=text_body,
            ))

            env = await _recv_skip_ping(ws_b, timeout=1)
            msg = env.chat_message

            # 客户端设置的字段
            assert msg.client_msg_id == cid
            assert msg.sender_id == "field_alice"
            assert msg.receiver_id == "field_bob"
            assert msg.channel == ChannelType.PRIVATE
            assert msg.content_type == ContentType.TEXT
            assert msg.text.text == text_body
            # 服务端补充的字段
            assert msg.server_msg_id > 0
            assert msg.created_at > 0

    # -------------------------------------------------------------- #
    #  4. 连续消息按序到达
    # -------------------------------------------------------------- #

    async def test_multiple_messages_arrive_in_order(self, ws_base_url: str):
        """连续发送 5 条消息，接收端全部按序到达。"""
        n = 5

        async with (
            ws_connect(
                _ws_uri(ws_base_url, "order_alice"), ping_interval=None,
            ) as ws_a,
            ws_connect(
                _ws_uri(ws_base_url, "order_bob"), ping_interval=None,
            ) as ws_b,
        ):
            await asyncio.sleep(0.3)

            sent_ids: list[str] = []
            for i in range(n):
                cid = f"order_{i}_{uuid.uuid4().hex[:8]}"
                sent_ids.append(cid)
                await ws_a.send(_build_text_envelope(
                    client_msg_id=cid,
                    sender_id="order_alice",
                    receiver_id="order_bob",
                    text=f"第 {i} 条消息",
                ))

            received_ids: list[str] = []
            for _ in range(n):
                env = await _recv_skip_ping(ws_b, timeout=2)
                assert env.type == EnvelopeType.CHAT_MESSAGE
                received_ids.append(env.chat_message.client_msg_id)

            assert received_ids == sent_ids, (
                f"消息顺序不一致: sent={sent_ids}, received={received_ids}"
            )

    # -------------------------------------------------------------- #
    #  5. 双向通信
    # -------------------------------------------------------------- #

    async def test_bidirectional_messaging(self, ws_base_url: str):
        """A→B 和 B→A 双向收发均正常。"""
        cid_a2b = uuid.uuid4().hex
        cid_b2a = uuid.uuid4().hex

        async with (
            ws_connect(
                _ws_uri(ws_base_url, "bi_alice"), ping_interval=None,
            ) as ws_a,
            ws_connect(
                _ws_uri(ws_base_url, "bi_bob"), ping_interval=None,
            ) as ws_b,
        ):
            await asyncio.sleep(0.3)

            # ① Alice → Bob
            await ws_a.send(_build_text_envelope(
                client_msg_id=cid_a2b,
                sender_id="bi_alice",
                receiver_id="bi_bob",
                text="Hello Bob!",
            ))

            env_at_bob = await _recv_skip_ping(ws_b, timeout=1)
            assert env_at_bob.type == EnvelopeType.CHAT_MESSAGE
            assert env_at_bob.chat_message.text.text == "Hello Bob!"

            # 消耗 Alice 的 ACK (来自她自己的消息)
            ack_a = await _recv_skip_ping(ws_a, timeout=1)
            assert ack_a.type == EnvelopeType.MESSAGE_ACK
            assert ack_a.message_ack.client_msg_id == cid_a2b

            # ② Bob → Alice
            await ws_b.send(_build_text_envelope(
                client_msg_id=cid_b2a,
                sender_id="bi_bob",
                receiver_id="bi_alice",
                text="Hello Alice!",
            ))

            env_at_alice = await _recv_skip_ping(ws_a, timeout=1)
            assert env_at_alice.type == EnvelopeType.CHAT_MESSAGE
            assert env_at_alice.chat_message.text.text == "Hello Alice!"

            # 消耗 Bob 的 ACK
            ack_b = await _recv_skip_ping(ws_b, timeout=1)
            assert ack_b.type == EnvelopeType.MESSAGE_ACK
            assert ack_b.message_ack.client_msg_id == cid_b2a
