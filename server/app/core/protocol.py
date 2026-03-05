"""
Protobuf Envelope 构建 / 解析辅助层。

所有 WS 帧严格使用 protobuf 二进制，严禁 JSON。
上层通过本模块构造和解析 Envelope，不直接操作 pb2 类。
"""

from __future__ import annotations

import time
import uuid

from app.generated.chat_pb2 import (
    AckStatus,
    ChatMessage,
    Envelope,
    EnvelopeType,
    ErrorNotice,
    KickNotice,
    MessageAck,
    Ping,
    Pong,
    SyncResponse,
)


def _new_envelope(env_type: EnvelopeType.ValueType, **payload_kwargs) -> Envelope:
    return Envelope(
        type=env_type,
        timestamp_ms=int(time.time() * 1000),
        request_id=uuid.uuid4().hex,
        **payload_kwargs,
    )


# -------------------- 构建器 --------------------

def build_ping() -> bytes:
    return _new_envelope(EnvelopeType.PING, ping=Ping()).SerializeToString()


def build_pong() -> bytes:
    return _new_envelope(EnvelopeType.PONG, pong=Pong()).SerializeToString()


def build_message_ack(
    client_msg_id: str,
    server_msg_id: int,
    status: AckStatus.ValueType,
) -> bytes:
    return _new_envelope(
        EnvelopeType.MESSAGE_ACK,
        message_ack=MessageAck(
            client_msg_id=client_msg_id,
            server_msg_id=server_msg_id,
            status=status,
        ),
    ).SerializeToString()


def build_kick(reason: str, device_id: str) -> bytes:
    return _new_envelope(
        EnvelopeType.KICK_NOTICE,
        kick_notice=KickNotice(reason=reason, device_id=device_id),
    ).SerializeToString()


def build_error(code: int, message: str) -> bytes:
    return _new_envelope(
        EnvelopeType.ERROR_NOTICE,
        error_notice=ErrorNotice(code=code, message=message),
    ).SerializeToString()


def build_chat_message_envelope(chat_msg: ChatMessage) -> bytes:
    """将 ChatMessage 包装为 Envelope 二进制 (用于服务端转发)。"""
    return _new_envelope(
        EnvelopeType.CHAT_MESSAGE,
        chat_message=chat_msg,
    ).SerializeToString()


def build_sync_response(
    messages: list[ChatMessage],
    has_more: bool,
) -> bytes:
    return _new_envelope(
        EnvelopeType.SYNC_RESPONSE,
        sync_response=SyncResponse(messages=messages, has_more=has_more),
    ).SerializeToString()


# -------------------- 解析器 --------------------

def parse_envelope(data: bytes) -> Envelope:
    envelope = Envelope()
    envelope.ParseFromString(data)
    return envelope
