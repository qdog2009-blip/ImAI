"""
Protobuf 辅助层 — 封装 chat_pb2 的导入与 Envelope 构建/解析。

优先从插件目录的 chat_pb2.py 加载，找不到时尝试从相邻的 server 目录加载。
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

# 尝试导入 chat_pb2：先插件目录，再 server 目录
_plugin_dir = Path(__file__).resolve().parent
_server_generated = _plugin_dir.parent / "server" / "app" / "generated"

for _path in (_plugin_dir, _server_generated):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from chat_pb2 import (  # noqa: E402
    AckStatus,
    AuthRequest,
    ChatMessage,
    ChannelType,
    ContentType,
    DeviceType,
    Envelope,
    EnvelopeType,
    Ping,
    Pong,
    SyncRequest,
    TextContent,
)


# ------------------------------------------------------------------ #
#  构建器
# ------------------------------------------------------------------ #

def _new_envelope(env_type: int, **payload_kwargs) -> Envelope:
    return Envelope(
        type=env_type,
        timestamp_ms=int(time.time() * 1000),
        request_id=uuid.uuid4().hex,
        **payload_kwargs,
    )


def build_auth_request(server_secret_key: str, token: str, device_id: str) -> bytes:
    return _new_envelope(
        EnvelopeType.AUTH_REQUEST,
        auth_request=AuthRequest(
            server_secret_key=server_secret_key,
            token=token,
            device_id=device_id,
            device_type=DeviceType.DESKTOP,
        ),
    ).SerializeToString()


def build_pong() -> bytes:
    return _new_envelope(EnvelopeType.PONG, pong=Pong()).SerializeToString()


def build_sync_request(last_server_msg_id: int = 0, limit: int = 50) -> bytes:
    return _new_envelope(
        EnvelopeType.SYNC_REQUEST,
        sync_request=SyncRequest(
            last_server_msg_id=last_server_msg_id,
            limit=limit,
        ),
    ).SerializeToString()


def build_chat_message(
    client_msg_id: str,
    sender_id: str,
    receiver_id: str,
    text: str,
) -> bytes:
    msg = ChatMessage(
        client_msg_id=client_msg_id,
        sender_id=sender_id,
        receiver_id=receiver_id,
        channel=ChannelType.PRIVATE,
        content_type=ContentType.TEXT,
        text=TextContent(text=text),
    )
    return _new_envelope(
        EnvelopeType.CHAT_MESSAGE,
        chat_message=msg,
    ).SerializeToString()


# ------------------------------------------------------------------ #
#  解析器
# ------------------------------------------------------------------ #

def parse_envelope(data: bytes) -> Envelope:
    envelope = Envelope()
    envelope.ParseFromString(data)
    return envelope


# ------------------------------------------------------------------ #
#  重导出常用常量
# ------------------------------------------------------------------ #

__all__ = [
    "AckStatus",
    "ChannelType",
    "ContentType",
    "EnvelopeType",
    "build_auth_request",
    "build_chat_message",
    "build_pong",
    "build_sync_request",
    "parse_envelope",
]
