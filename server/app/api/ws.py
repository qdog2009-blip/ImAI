"""
WebSocket API 端点 — protobuf Envelope 收发循环，严禁 JSON。
"""

from __future__  import annotations

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from google.protobuf.message import DecodeError

from app.core.protocol import (
    build_chat_message_envelope,
    build_error,
    build_message_ack,
    build_sync_response,
    parse_envelope,
)
from app.core.ws import Connection, ConnectionManager
from app.generated.chat_pb2 import AckStatus, EnvelopeType
from app.services.message import MessageService

logger = logging.getLogger(__name__)

router = APIRouter()

# 由 main.py lifespan 注入
manager: ConnectionManager = None  # type: ignore[assignment]
message_service: MessageService = None  # type: ignore[assignment]


def init_router(mgr: ConnectionManager, msg_svc: MessageService) -> None:
    global manager, message_service
    manager = mgr
    message_service = msg_svc


@router.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    user_id: str = Query(...),
    device_id: str = Query(""),
) -> None:
    conn = await manager.connect(ws, user_id, device_id)
    try:
        while True:
            data: bytes = await ws.receive_bytes()
            await _dispatch(conn, data)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(conn)


# ------------------------------------------------------------------ #
#  Envelope 分发路由
# ------------------------------------------------------------------ #

async def _dispatch(conn: Connection, data: bytes) -> None:
    try:
        envelope = parse_envelope(data)
    except DecodeError:
        await manager.send_to_connection(
            conn, build_error(4000, "invalid_protobuf"),
        )
        return

    match int(envelope.type):
        case EnvelopeType.AUTH_REQUEST:
            pass  # 鉴权已通过 query param 完成，此帧为客户端扩展预留，忽略

        case EnvelopeType.PONG:
            conn.record_pong()

        case EnvelopeType.CHAT_MESSAGE:
            await _handle_chat_message(conn, envelope)

        case EnvelopeType.SYNC_REQUEST:
            await _handle_sync_request(conn, envelope)

        case EnvelopeType.READ_RECEIPT:
            await _handle_read_receipt(conn, envelope, data)

        case EnvelopeType.BOT_REQUEST:
            logger.info("ws.bot_request  user=%s", conn.user_id)

        case _:
            await manager.send_to_connection(
                conn,
                build_error(4001, f"unsupported_type:{envelope.type}"),
            )


# ------------------------------------------------------------------ #
#  ChatMessage — 防重 → Write-First 落库 → ACK → 在线推送
# ------------------------------------------------------------------ #

async def _handle_chat_message(conn: Connection, envelope) -> None:
    msg = envelope.chat_message
    client_msg_id = msg.client_msg_id

    if not client_msg_id:
        await manager.send_to_connection(
            conn, build_error(4002, "missing_client_msg_id"),
        )
        return

    # ① 防重防抖
    if await manager.is_duplicate(client_msg_id):
        logger.debug("ws.dedup  cid=%s", client_msg_id)
        await manager.send_to_connection(
            conn,
            build_message_ack(client_msg_id, 0, AckStatus.SENT),
        )
        return

    # ② Write-First 异步落库
    server_msg_id, created_at = await message_service.persist(msg)

    # ③ ACK → 发送方 (携带真实 server_msg_id)
    await manager.send_to_connection(
        conn,
        build_message_ack(client_msg_id, server_msg_id, AckStatus.SENT),
    )

    # ④ 在线推送 → 接收方，并记录已投递水位线防止 SyncResponse 重复下发
    receiver_id = msg.receiver_id
    if receiver_id and manager.is_online(receiver_id):
        msg.server_msg_id = server_msg_id
        msg.created_at = created_at
        await manager.send_to_user(
            receiver_id, build_chat_message_envelope(msg),
        )
        await message_service.mark_delivered(receiver_id, server_msg_id)

    logger.debug(
        "ws.chat  %s→%s  smid=%d  cid=%s",
        conn.user_id, receiver_id, server_msg_id, client_msg_id,
    )


# ------------------------------------------------------------------ #
#  SyncRequest — 水位线拉取离线消息
# ------------------------------------------------------------------ #

async def _handle_sync_request(conn: Connection, envelope) -> None:
    req = envelope.sync_request
    messages, has_more = await message_service.sync(
        user_id=conn.user_id,
        last_server_msg_id=req.last_server_msg_id,
        limit=req.limit or 50,
    )
    await manager.send_to_connection(
        conn, build_sync_response(messages, has_more),
    )
    logger.debug(
        "ws.sync  user=%s waterline=%d count=%d more=%s",
        conn.user_id, req.last_server_msg_id, len(messages), has_more,
    )


# ------------------------------------------------------------------ #
#  ReadReceipt — 转发给会话对端
# ------------------------------------------------------------------ #

async def _handle_read_receipt(conn: Connection, envelope, raw: bytes) -> None:
    receipt = envelope.read_receipt
    if receipt.conversation_id:
        await manager.send_to_user(receipt.conversation_id, raw)
