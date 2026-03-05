"""
消息 ORM 模型 — Write-First 异步落库的持久化载体。

server_msg_id: 自增主键，单调递增，作为水位线同步基准。
body:          ChatMessage 完整 protobuf 序列化，零解析直写。
"""

from __future__ import annotations

from sqlalchemy import BigInteger, Index, Integer, LargeBinary, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# SQLite 的 AUTOINCREMENT 仅对 INTEGER 生效；PostgreSQL 用 BIGSERIAL
_ServerMsgIdType = BigInteger().with_variant(Integer, "sqlite")


class Message(Base):
    __tablename__ = "messages"

    server_msg_id: Mapped[int] = mapped_column(
        _ServerMsgIdType, primary_key=True, autoincrement=True,
    )
    client_msg_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False,
    )
    sender_id: Mapped[str] = mapped_column(String(64), nullable=False)
    receiver_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    content_type: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_msg_receiver_waterline", "receiver_id", "server_msg_id"),
        Index("ix_msg_sender", "sender_id"),
    )
