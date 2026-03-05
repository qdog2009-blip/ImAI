"""
异步数据库引擎 + 会话工厂。

根据 settings.DB_TYPE 自动适配:
  - sqlite:   aiosqlite 驱动，开启 WAL 模式
  - postgres:  asyncpg 驱动
"""

from __future__ import annotations

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=settings.APP_ENV == "development",
    pool_pre_ping=True,
)

# SQLite: WAL 模式 + 外键约束
if settings.DB_TYPE == "sqlite":
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)
