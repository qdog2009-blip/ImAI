"""
消息服务 — Write-First 异步落库 + 水位线同步查询。

Write-First 流程:
  客户端 ChatMessage ──▶ INSERT DB (获得 server_msg_id) ──▶ ACK ──▶ 在线推送

水位线同步:
  客户端 SyncRequest(last_server_msg_id) ──▶ SELECT WHERE smid > ? ──▶ SyncResponse
"""

from __future__ import annotations

import logging
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.generated.chat_pb2 import ChatMessage as ChatMessagePb
from app.models.message import Message

logger = logging.getLogger(__name__)


class MessageService:

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    # ================================================================ #
    #  Write-First 异步落库
    # ================================================================ #

    async def persist(self, chat_msg: ChatMessagePb) -> tuple[int, int]:
        """
        Write-First: 立即持久化，返回 (server_msg_id, created_at)。

        body 列存储 ChatMessage 完整 protobuf，零拆解直写。
        """
        now_ms = int(time.time() * 1000)

        row = Message(
            client_msg_id=chat_msg.client_msg_id,
            sender_id=chat_msg.sender_id,
            receiver_id=chat_msg.receiver_id,
            channel=int(chat_msg.channel),
            content_type=int(chat_msg.content_type),
            body=chat_msg.SerializeToString(),
            created_at=now_ms,
        )

        async with self._session_factory() as session:
            async with session.begin():
                session.add(row)
            # begin() 退出时自动 commit；refresh 获取自增 ID
            await session.refresh(row)

        logger.debug(
            "msg.persist  smid=%d cid=%s %s→%s",
            row.server_msg_id,
            row.client_msg_id,
            row.sender_id,
            row.receiver_id,
        )
        return row.server_msg_id, now_ms

    # ================================================================ #
    #  水位线同步查询
    # ================================================================ #

    async def sync(
        self,
        user_id: str,
        last_server_msg_id: int,
        limit: int = 50,
    ) -> tuple[list[ChatMessagePb], bool]:
        """
        拉取 user_id 在 last_server_msg_id 之后的消息。

        返回 (messages, has_more):
          - messages: 按 server_msg_id 升序的 ChatMessage 列表
          - has_more: 服务端是否还有更早未拉取的数据
        """
        limit = min(max(limit, 1), 200)  # 限制 1~200

        async with self._session_factory() as session:
            stmt = (
                select(Message)
                .where(
                    Message.receiver_id == user_id,
                    Message.server_msg_id > last_server_msg_id,
                )
                .order_by(Message.server_msg_id)
                .limit(limit + 1)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()

        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        messages: list[ChatMessagePb] = []
        for row in rows:
            msg = ChatMessagePb()
            msg.ParseFromString(row.body)
            # 用 DB 权威值覆盖
            msg.server_msg_id = row.server_msg_id
            msg.created_at = row.created_at
            messages.append(msg)

        logger.debug(
            "msg.sync  user=%s waterline=%d count=%d has_more=%s",
            user_id, last_server_msg_id, len(messages), has_more,
        )
        return messages, has_more
