"""
认证模块功能测试 — 注册 / 登录 / JWT 校验。

覆盖场景:
  1. 注册成功 + 返回有效 JWT
  2. 重复注册 → 409
  3. 用户名太短 / 密码太短 → 422
  4. 登录成功 + 返回有效 JWT
  5. 错误密码 → 401
  6. 不存在的用户 → 401
  7. JWT 访问 /api/me 成功
  8. 无 token / 伪造 token → 401
  9. 过期 token → 401
  10. 服务器密钥校验 (已有功能)
"""

from __future__ import annotations

import time

import jwt
import pytest
from httpx import AsyncClient

from app.core.config import settings
from app.core.security import create_access_token


# ================================================================== #
#  注册
# ================================================================== #


class TestRegister:

    async def test_register_success(self, client: AsyncClient):
        resp = await client.post("/api/register", json={
            "username": "alice",
            "password": "Pass@123",
            "nickname": "Alice",
        })
        assert resp.status_code == 201

        body = resp.json()
        assert body["username"] == "alice"
        assert body["nickname"] == "Alice"
        assert body["user_id"]
        assert body["access_token"]
        assert body["token_type"] == "bearer"

    async def test_register_returns_valid_jwt(self, client: AsyncClient):
        resp = await client.post("/api/register", json={
            "username": "bob",
            "password": "Pass@123",
        })
        token = resp.json()["access_token"]

        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        assert payload["sub"]  # user_id
        assert payload["username"] == "bob"
        assert payload["exp"] > time.time()

    async def test_register_default_nickname(self, client: AsyncClient):
        """nickname 未传时，默认等于 username。"""
        resp = await client.post("/api/register", json={
            "username": "charlie",
            "password": "Pass@123",
        })
        assert resp.status_code == 201
        assert resp.json()["nickname"] == "charlie"

    async def test_register_duplicate_username(self, client: AsyncClient):
        payload = {"username": "dup", "password": "Pass@123"}
        resp1 = await client.post("/api/register", json=payload)
        assert resp1.status_code == 201

        resp2 = await client.post("/api/register", json=payload)
        assert resp2.status_code == 409

    async def test_register_short_username(self, client: AsyncClient):
        resp = await client.post("/api/register", json={
            "username": "a",
            "password": "Pass@123",
        })
        assert resp.status_code == 422

    async def test_register_short_password(self, client: AsyncClient):
        resp = await client.post("/api/register", json={
            "username": "valid_user",
            "password": "12345",
        })
        assert resp.status_code == 422


# ================================================================== #
#  登录
# ================================================================== #


class TestLogin:

    async def test_login_success(self, client: AsyncClient):
        # 先注册
        await client.post("/api/register", json={
            "username": "login_user",
            "password": "Pass@123",
        })

        # 再登录
        resp = await client.post("/api/login", json={
            "username": "login_user",
            "password": "Pass@123",
        })
        assert resp.status_code == 200

        body = resp.json()
        assert body["access_token"]
        assert body["username"] == "login_user"

    async def test_login_returns_valid_jwt(self, client: AsyncClient):
        await client.post("/api/register", json={
            "username": "jwt_user",
            "password": "Pass@123",
        })
        resp = await client.post("/api/login", json={
            "username": "jwt_user",
            "password": "Pass@123",
        })
        token = resp.json()["access_token"]

        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        assert payload["sub"]
        assert payload["username"] == "jwt_user"
        assert payload["exp"] > time.time()

    async def test_login_wrong_password(self, client: AsyncClient):
        await client.post("/api/register", json={
            "username": "wrong_pw",
            "password": "Pass@123",
        })
        resp = await client.post("/api/login", json={
            "username": "wrong_pw",
            "password": "WrongPass",
        })
        assert resp.status_code == 401

    async def test_login_nonexistent_user(self, client: AsyncClient):
        resp = await client.post("/api/login", json={
            "username": "ghost",
            "password": "Pass@123",
        })
        assert resp.status_code == 401


# ================================================================== #
#  JWT 验证 — GET /api/me
# ================================================================== #


class TestMe:

    async def test_me_with_valid_token(
        self,
        client: AsyncClient,
        registered_user: dict,
        auth_headers: dict,
    ):
        resp = await client.get("/api/me", headers=auth_headers)
        assert resp.status_code == 200

        body = resp.json()
        assert body["user_id"] == registered_user["user_id"]
        assert body["username"] == "testuser"
        assert body["nickname"] == "Tester"

    async def test_me_without_token(self, client: AsyncClient):
        resp = await client.get("/api/me")
        assert resp.status_code == 401

    async def test_me_with_forged_token(self, client: AsyncClient):
        fake_token = jwt.encode(
            {"sub": "fake_id", "exp": time.time() + 3600},
            "wrong_secret",
            algorithm="HS256",
        )
        resp = await client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {fake_token}"},
        )
        assert resp.status_code == 401

    async def test_me_with_expired_token(self, client: AsyncClient):
        expired = create_access_token(
            {"sub": "some_id", "username": "x"},
            expires_seconds=-1,
        )
        resp = await client.get(
            "/api/me",
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert resp.status_code == 401


# ================================================================== #
#  服务器密钥校验 (原有端点)
# ================================================================== #


class TestValidate:

    async def test_validate_correct_key(self, client: AsyncClient):
        resp = await client.post("/api/validate", json={
            "server_secret_key": settings.SERVER_SECRET_KEY,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["valid"] is True
        assert body["server_name"] == settings.APP_NAME

    async def test_validate_wrong_key(self, client: AsyncClient):
        resp = await client.post("/api/validate", json={
            "server_secret_key": "definitely_wrong_key",
        })
        assert resp.status_code == 403
