"""用户 ORM 模型 — 注册 / 登录 / JWT 认证载体。"""

from __future__ import annotations

import uuid

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(32), primary_key=True, default=_uuid,
    )
    username: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True,
    )
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    nickname: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False)
