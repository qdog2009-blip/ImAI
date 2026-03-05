"""
Alembic env — 异步引擎支持 (asyncpg / aiosqlite)。

sqlalchemy.url 从 app.core.config.settings 动态读取，
无需在 alembic.ini 中硬编码连接字符串。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.core.config import settings
from app.models.base import Base

# ---- 确保所有 ORM 模型被导入，Alembic 才能检测到它们 ----
import app.models.user  # noqa: F401
import app.models.message  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 动态注入数据库 URL
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


# ------------------------------------------------------------------ #
#  离线模式 (生成 SQL 文件，不连接数据库)
# ------------------------------------------------------------------ #

def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ------------------------------------------------------------------ #
#  在线模式 (通过异步引擎连接数据库)
# ------------------------------------------------------------------ #

def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
