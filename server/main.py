"""
ImAI 后端入口 — FastAPI + websockets (uvicorn)。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.ws import ConnectionManager
from app.models.base import Base
from app.services.cache import create_cache_provider
from app.services.message import MessageService

logging.basicConfig(
    level=settings.APP_LOG_LEVEL,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期: 启动时初始化，关闭时释放资源。"""

    # ---- startup ----
    # 建表 (开发阶段；生产环境应使用 Alembic 迁移)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    cache = create_cache_provider()
    manager = ConnectionManager(cache=cache)
    msg_svc = MessageService(session_factory=AsyncSessionLocal, cache=cache)

    from app.api.ws import init_router
    init_router(manager, msg_svc)

    app.state.cache = cache
    app.state.ws_manager = manager
    app.state.message_service = msg_svc

    yield

    # ---- shutdown ----
    await manager.close_all()
    await cache.close()
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.APP_DEBUG,
    lifespan=lifespan,
)

# CORS 中间件 (允许所有来源)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
from app.api.auth import router as auth_router  # noqa: E402
from app.api.ws import router as ws_router  # noqa: E402
from app.api.upload import router as upload_router  # noqa: E402
from app.api.users import router as users_router  # noqa: E402
app.include_router(auth_router)
app.include_router(ws_router)
app.include_router(upload_router)
app.include_router(users_router)

# 静态文件服务 - 上传的文件
upload_dir = Path(settings.LOCAL_STORAGE_DIR)
if upload_dir.exists():
    app.mount("/uploads", StaticFiles(directory=str(upload_dir)), name="uploads")


if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        ws="websockets",
        ws_ping_interval=None,
        ws_ping_timeout=None,
        log_level=settings.APP_LOG_LEVEL.lower(),
        reload=settings.APP_ENV == "development",
    )