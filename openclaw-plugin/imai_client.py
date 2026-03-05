"""
ImAI WebSocket 客户端 — 连接到 ImAI 服务器，处理 Protobuf 消息收发。

职责:
  1. REST 认证 (validate / login)
  2. WebSocket 连接与重连
  3. 心跳 Ping → Pong 响应
  4. 收到 CHAT_MESSAGE 时通过回调通知上层
  5. 拉取离线消息 (SyncRequest)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Awaitable, Callable, Optional

import aiohttp
import websockets
from websockets import ClientConnection

from config import settings
from proto_helper import (
    EnvelopeType,
    build_auth_request,
    build_chat_message,
    build_pong,
    build_sync_request,
    parse_envelope,
)

logger = logging.getLogger(__name__)

# 消息回调类型: 接收到 ChatMessage 时调用
MessageCallback = Callable[["IncomingMessage"], Awaitable[None]]


class IncomingMessage:
    """封装收到的 ChatMessage，提供便捷属性。"""

    def __init__(self, chat_msg) -> None:
        self._msg = chat_msg

    @property
    def client_msg_id(self) -> str:
        return self._msg.client_msg_id

    @property
    def server_msg_id(self) -> int:
        return self._msg.server_msg_id

    @property
    def sender_id(self) -> str:
        return self._msg.sender_id

    @property
    def receiver_id(self) -> str:
        return self._msg.receiver_id

    @property
    def text(self) -> str:
        if self._msg.HasField("text"):
            return self._msg.text.text
        return ""

    @property
    def content_type(self) -> int:
        return self._msg.content_type

    def __repr__(self) -> str:
        return (
            f"<IncomingMessage sender={self.sender_id!r} "
            f"text={self.text!r:.40s}>"
        )


class ImAIClient:
    """
    ImAI 服务器客户端。

    用法::

        client = ImAIClient(on_message=handler)
        await client.run()          # 阻塞，自动重连
    """

    def __init__(self, on_message: MessageCallback) -> None:
        self._on_message = on_message
        self._user_id: str = ""
        self._access_token: str = ""
        self._ws: Optional[ClientConnection] = None
        self._last_server_msg_id: int = 0
        self._running = False

    # ================================================================ #
    #  公开接口
    # ================================================================ #

    async def run(self) -> None:
        """启动插件主循环，含自动重连。"""
        self._running = True
        attempt = 0

        while self._running:
            try:
                await self._authenticate()
                await self._connect_and_loop()
                attempt = 0
            except asyncio.CancelledError:
                break
            except Exception as exc:
                attempt += 1
                max_attempts = settings.MAX_RECONNECT_ATTEMPTS
                if max_attempts and attempt >= max_attempts:
                    logger.error(
                        "imai: 已达最大重连次数 %d，停止重连", max_attempts
                    )
                    break

                wait = min(settings.RECONNECT_INTERVAL * attempt, 60)
                logger.warning(
                    "imai: 连接异常 (%s)，%d 秒后第 %d 次重连…",
                    exc, wait, attempt,
                )
                await asyncio.sleep(wait)

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()

    async def send_text(self, receiver_id: str, text: str) -> None:
        """向指定用户发送文本消息。"""
        if not self._ws:
            logger.warning("imai: WebSocket 未连接，消息丢弃")
            return
        payload = build_chat_message(
            client_msg_id=uuid.uuid4().hex,
            sender_id=self._user_id,
            receiver_id=receiver_id,
            text=text,
        )
        await self._ws.send(payload)
        logger.debug("imai: 已发送消息 → %s", receiver_id)

    # ================================================================ #
    #  内部 — 认证
    # ================================================================ #

    async def _authenticate(self) -> None:
        """REST 认证: validate → login，获取 user_id 与 JWT。"""
        base = settings.IMAI_SERVER_URL.rstrip("/")

        async with aiohttp.ClientSession() as session:
            # 1. 校验服务器密钥
            async with session.post(
                f"{base}/api/validate",
                json={"server_secret_key": settings.IMAI_SERVER_SECRET_KEY},
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"imai: 服务器密钥校验失败 [{resp.status}] {body}")
                data = await resp.json()
                logger.info("imai: 服务器验证通过 — %s", data.get("server_name"))

            # 2. 登录获取 JWT
            async with session.post(
                f"{base}/api/login",
                json={
                    "username": settings.IMAI_USERNAME,
                    "password": settings.IMAI_PASSWORD,
                },
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise RuntimeError(f"imai: 登录失败 [{resp.status}] {body}")
                data = await resp.json()

        self._user_id = data["user_id"]
        self._access_token = data["access_token"]
        logger.info(
            "imai: 登录成功 — user_id=%s username=%s",
            self._user_id,
            data.get("username"),
        )

    # ================================================================ #
    #  内部 — WebSocket 连接与消息循环
    # ================================================================ #

    async def _connect_and_loop(self) -> None:
        ws_url = (
            f"{settings.ws_url_base}/ws"
            f"?user_id={self._user_id}"
            f"&device_id={settings.IMAI_DEVICE_ID}"
        )
        logger.info("imai: 正在连接 %s", ws_url)

        async with websockets.connect(ws_url, ping_interval=None) as ws:
            self._ws = ws
            logger.info("imai: WebSocket 已连接")

            # 发送认证帧 (服务端目前通过 query param 鉴权，此帧作扩展备用)
            auth_payload = build_auth_request(
                server_secret_key=settings.IMAI_SERVER_SECRET_KEY,
                token=self._access_token,
                device_id=settings.IMAI_DEVICE_ID,
            )
            await ws.send(auth_payload)

            # 拉取离线消息
            await ws.send(
                build_sync_request(
                    last_server_msg_id=self._last_server_msg_id,
                )
            )

            async for raw in ws:
                if isinstance(raw, bytes):
                    await self._dispatch(raw)

        self._ws = None

    async def _dispatch(self, data: bytes) -> None:
        try:
            envelope = parse_envelope(data)
        except Exception as exc:
            logger.warning("imai: protobuf 解析失败: %s", exc)
            return

        match envelope.type:
            case EnvelopeType.PING:
                await self._ws.send(build_pong())
                logger.debug("imai: Ping → Pong")

            case EnvelopeType.CHAT_MESSAGE:
                msg = envelope.chat_message
                # 更新水位线
                if msg.server_msg_id > self._last_server_msg_id:
                    self._last_server_msg_id = msg.server_msg_id
                incoming = IncomingMessage(msg)
                logger.info(
                    "imai: 收到消息 from=%s text=%.60s",
                    incoming.sender_id, incoming.text,
                )
                await self._on_message(incoming)

            case EnvelopeType.SYNC_RESPONSE:
                sync = envelope.sync_response
                logger.info(
                    "imai: 离线消息同步 count=%d has_more=%s",
                    len(sync.messages), sync.has_more,
                )
                for msg in sync.messages:
                    if msg.server_msg_id > self._last_server_msg_id:
                        self._last_server_msg_id = msg.server_msg_id
                    await self._on_message(IncomingMessage(msg))

            case EnvelopeType.AUTH_RESPONSE:
                resp = envelope.auth_response
                if resp.success:
                    logger.info("imai: AuthResponse 成功 user_id=%s", resp.user_id)
                else:
                    logger.warning("imai: AuthResponse 失败: %s", resp.message)

            case EnvelopeType.MESSAGE_ACK:
                ack = envelope.message_ack
                logger.debug(
                    "imai: MessageAck cid=%s smid=%d status=%s",
                    ack.client_msg_id, ack.server_msg_id, ack.status,
                )

            case EnvelopeType.KICK_NOTICE:
                kick = envelope.kick_notice
                logger.warning(
                    "imai: 被踢下线 reason=%s new_device=%s",
                    kick.reason, kick.device_id,
                )

            case EnvelopeType.ERROR_NOTICE:
                err = envelope.error_notice
                logger.warning(
                    "imai: 服务器错误 code=%d msg=%s",
                    err.code, err.message,
                )

            case EnvelopeType.PRESENCE_NOTIFY:
                notify = envelope.presence_notify
                logger.debug(
                    "imai: 在线状态变更 user=%s status=%s",
                    notify.user_id, notify.status,
                )

            case _:
                logger.debug("imai: 未处理的消息类型 type=%d", envelope.type)
