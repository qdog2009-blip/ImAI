"""
用户列表 API。

GET /users — 获取所有用户列表
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.models.user import User

router = APIRouter(prefix="/api")


# ================================================================== #
#  用户列表响应模型
# ================================================================== #

class UserInfo(BaseModel):
    user_id: str
    username: str
    nickname: str


class UsersResponse(BaseModel):
    users: list[UserInfo]


# ================================================================== #
#  获取用户列表
# ================================================================== #

@router.get("/users", response_model=UsersResponse)
async def get_users() -> UsersResponse:
    """获取所有用户列表（排除当前用户）。"""
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User))
        users = result.scalars().all()

        user_list = [
            UserInfo(
                user_id=user.id,
                username=user.username,
                nickname=user.nickname or user.username,
            )
            for user in users
        ]

    return UsersResponse(users=user_list)