"""
认证相关 REST 端点。

POST /api/validate  — 登录前校验服务器地址 + 密钥连通性
POST /api/register  — 注册新用户
POST /api/login     — 登录获取 JWT
GET  /api/me        — 验证 JWT 并返回当前用户信息
"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.security import (
    create_access_token,
    get_current_user_id,
    hash_password,
    verify_password,
)
from app.models.user import User

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ #
#  依赖: 获取异步 DB 会话
# ------------------------------------------------------------------ #

async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


# ================================================================== #
#  1. 服务器密钥校验 (登录前)
# ================================================================== #

class ValidateRequest(BaseModel):
    server_secret_key: str


class ValidateResponse(BaseModel):
    valid: bool
    server_name: str


@router.post("/validate", response_model=ValidateResponse)
async def validate_server(body: ValidateRequest) -> ValidateResponse:
    if not settings.SERVER_SECRET_KEY:
        raise HTTPException(500, "server_secret_key_not_configured")

    key_ok = hmac.compare_digest(
        body.server_secret_key,
        settings.SERVER_SECRET_KEY,
    )
    if not key_ok:
        raise HTTPException(403, "invalid_server_secret_key")

    return ValidateResponse(valid=True, server_name=settings.APP_NAME)


# ================================================================== #
#  2. 注册
# ================================================================== #

class RegisterRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=6, max_length=128)
    nickname: str = Field(default="", max_length=64)


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    nickname: str


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(_get_db),
) -> AuthResponse:
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        nickname=body.nickname or body.username,
        created_at=int(time.time()),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "username_already_exists",
        )
    await db.refresh(user)

    token = create_access_token({"sub": user.id, "username": user.username})
    return AuthResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        nickname=user.nickname,
    )


# ================================================================== #
#  3. 登录
# ================================================================== #

class LoginRequest(BaseModel):
    username: str
    password: str


@router.post("/login", response_model=AuthResponse)
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(_get_db),
) -> AuthResponse:
    result = await db.execute(
        select(User).where(User.username == body.username),
    )
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "invalid_credentials",
        )

    token = create_access_token({"sub": user.id, "username": user.username})
    return AuthResponse(
        access_token=token,
        user_id=user.id,
        username=user.username,
        nickname=user.nickname,
    )


# ================================================================== #
#  4. 获取当前用户 (JWT 验证)
# ================================================================== #

class MeResponse(BaseModel):
    user_id: str
    username: str
    nickname: str


@router.get("/me", response_model=MeResponse)
async def get_me(
    user_id: str = Depends(get_current_user_id),
    db: AsyncSession = Depends(_get_db),
) -> MeResponse:
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user_not_found")

    return MeResponse(
        user_id=user.id,
        username=user.username,
        nickname=user.nickname,
    )
