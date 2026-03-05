"""安全工具 — 密码哈希 + JWT 签发 / 验证 + FastAPI 依赖。"""

from __future__ import annotations

import time
from typing import Any

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.core.config import settings

# ------------------------------------------------------------------ #
#  密码哈希 (直接调用 bcrypt，不依赖已停止维护的 passlib)
# ------------------------------------------------------------------ #


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


# ------------------------------------------------------------------ #
#  JWT
# ------------------------------------------------------------------ #


def create_access_token(
    data: dict[str, Any],
    expires_seconds: int | None = None,
) -> str:
    """签发 JWT，默认有效期取自 settings。"""
    expire = int(time.time()) + (
        expires_seconds
        if expires_seconds is not None
        else settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )
    payload = {**data, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    """解码 JWT，失败抛出 HTTPException 401。"""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token_expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token")


# ------------------------------------------------------------------ #
#  FastAPI 依赖
# ------------------------------------------------------------------ #

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login")


async def get_current_user_id(token: str = Depends(oauth2_scheme)) -> str:
    """从 Bearer token 中提取 user_id，注入到路由函数参数。"""
    payload = decode_access_token(token)
    user_id: str | None = payload.get("sub")
    if not user_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid_token_payload")
    return user_id
