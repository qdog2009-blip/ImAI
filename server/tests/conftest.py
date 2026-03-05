"""
测试夹具 — .env.test 加载 + Alembic 自动建表/清表 + httpx 异步客户端。

执行顺序:
  1. 模块加载阶段: load_dotenv(.env.test) → 刷新 settings 单例
  2. session fixture: Alembic upgrade head → 跑全部测试 → downgrade base
  3. function fixture: 每个用例获得独立 AsyncClient

运行方式:
  正式 (Postgres):  docker compose -f devops/docker-compose.test.yml up -d --wait
                     cd server && pytest
  本地快验 (SQLite): DB_TYPE=sqlite CACHE_TYPE=file pytest
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ================================================================== #
#  第 0 步: 在任何 app 模块导入之前加载 .env.test
#  override=False → 外部环境变量优先于 .env.test (允许 CLI 覆盖)
# ================================================================== #

_project_root = Path(__file__).resolve().parents[2]  # server/../ → ImAI_cc/
_env_test = _project_root / ".env.test"

# 如果环境变量未预设 APP_ENV，才从 .env.test 加载
if not os.environ.get("APP_ENV"):
    load_dotenv(_env_test, override=True)
else:
    load_dotenv(_env_test, override=False)

# 确保 APP_ENV=test
os.environ.setdefault("APP_ENV", "test")

# 强制刷新 settings 单例 (清除 lru_cache，重新从环境变量读取)
from app.core.config import get_settings  # noqa: E402

get_settings.cache_clear()
_settings = get_settings()

# 安全断言: 确保加载的是测试配置
assert _settings.APP_ENV == "test", (
    f"conftest 期望 APP_ENV=test，实际为 {_settings.APP_ENV!r}。"
    f"请检查 .env.test 是否存在于 {_project_root}"
)

# ================================================================== #
#  现在可以安全导入 app 模块
# ================================================================== #

import pytest  # noqa: E402
import sqlalchemy  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

_server_root = _project_root / "server"
_is_postgres = _settings.DB_TYPE == "postgres"


# ------------------------------------------------------------------ #
#  Alembic 配置辅助
# ------------------------------------------------------------------ #

def _alembic_cfg() -> Config:
    cfg = Config(str(_server_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(_server_root / "alembic"))
    return cfg


# ================================================================== #
#  session 级: Alembic upgrade head / downgrade base
# ================================================================== #

@pytest.fixture(scope="session", autouse=True)
def db_schema():
    """
    测试会话开始时:  alembic upgrade head  → 建表
    测试会话结束后:  alembic downgrade base → 销毁全部表
    """
    cfg = _alembic_cfg()
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


# ================================================================== #
#  function 级: 每个用例清空数据
# ================================================================== #

@pytest.fixture(autouse=True)
async def clean_tables():
    """每个测试用例结束后清空所有业务表数据，保证用例间隔离。"""
    yield

    from app.core.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        if _is_postgres:
            # PostgreSQL: TRUNCATE CASCADE 快且重置序列
            await session.execute(
                sqlalchemy.text(
                    "TRUNCATE TABLE users, messages RESTART IDENTITY CASCADE"
                )
            )
        else:
            # SQLite: 无 TRUNCATE，用 DELETE
            await session.execute(sqlalchemy.text("DELETE FROM users"))
            await session.execute(sqlalchemy.text("DELETE FROM messages"))
        await session.commit()


# ================================================================== #
#  function 级: httpx AsyncClient
# ================================================================== #

@pytest.fixture
async def client():
    """每个测试用例获得一个干净的 httpx.AsyncClient。"""
    from main import app

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ================================================================== #
#  辅助 fixture: 已注册用户 + JWT
# ================================================================== #

@pytest.fixture
async def registered_user(client: AsyncClient) -> dict:
    """
    注册一个测试用户，返回完整响应体:
    {access_token, token_type, user_id, username, nickname}
    """
    resp = await client.post("/api/register", json={
        "username": "testuser",
        "password": "Test@12345",
        "nickname": "Tester",
    })
    assert resp.status_code == 201
    return resp.json()


@pytest.fixture
async def auth_headers(registered_user: dict) -> dict[str, str]:
    """Bearer token 请求头，直接用于 client.get(headers=...)。"""
    return {"Authorization": f"Bearer {registered_user['access_token']}"}
