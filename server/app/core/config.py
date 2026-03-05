"""
全局配置 — 基于 pydantic-settings，从 .env 文件加载。
严禁在代码中硬编码任何敏感信息。

用法:
    from app.core.config import settings
    print(settings.DB_TYPE)          # "sqlite" | "postgres"
    print(settings.database_url)     # 自动拼接的异步数据库 URL
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# project_root = server/../ → 即 .env 所在目录
PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """从 .env 读取全部环境变量，字段名与 .env 键一一对应。"""

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -------------------- 应用基础 --------------------
    APP_NAME: str = "ImAI"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    APP_DEBUG: bool = True
    APP_HOST: str = "0.0.0.0"
    APP_PORT: int = 8000
    APP_LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # -------------------- 安全与认证 --------------------
    SERVER_SECRET_KEY: str = ""
    JWT_SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # -------------------- 数据库 (策略+工厂模式) --------------------
    DB_TYPE: Literal["sqlite", "postgres"] = "sqlite"

    # SQLite
    SQLITE_PATH: str = "./data/imai.db"

    # PostgreSQL
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = ""
    POSTGRES_PASSWORD: str = ""
    POSTGRES_DB: str = "imai"

    # -------------------- 缓存 (策略+工厂模式) --------------------
    CACHE_TYPE: Literal["file", "redis"] = "file"

    # 文件缓存
    FILE_CACHE_DIR: str = "./data/cache"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0

    # -------------------- 存储 --------------------
    STORAGE_TYPE: Literal["local", "s3"] = "local"

    LOCAL_STORAGE_DIR: str = "./data/uploads"

    S3_ENDPOINT: str = ""
    S3_ACCESS_KEY: str = ""
    S3_SECRET_KEY: str = ""
    S3_BUCKET_NAME: str = ""
    S3_REGION: str = ""

    # -------------------- LLM / AI 机器人 --------------------
    LLM_PROVIDER: Literal["openclaw", "deepseek", "openai"] = "openclaw"
    LLM_API_BASE_URL: str = ""
    LLM_API_KEY: str = ""
    LLM_MODEL_NAME: str = ""
    LLM_TIMEOUT: int = 30
    LLM_STREAM_ENABLED: bool = False

    # -------------------- 推送服务 --------------------
    PUSH_PROVIDER: Literal["none", "jpush", "mobpush"] = "none"
    JPUSH_APP_KEY: str = ""
    JPUSH_MASTER_SECRET: str = ""
    MOBPUSH_APP_KEY: str = ""
    MOBPUSH_APP_SECRET: str = ""

    # -------------------- Webhook --------------------
    WEBHOOK_ENABLED: bool = False
    WEBHOOK_URL: str = ""
    WEBHOOK_SECRET: str = ""

    # -------------------- WebSocket --------------------
    WS_HEARTBEAT_INTERVAL: int = 30
    WS_MAX_CONNECTIONS_PER_USER: int = 3

    # -------------------- 派生属性 --------------------

    @property
    def database_url(self) -> str:
        """根据 DB_TYPE 自动拼接异步数据库连接 URL。"""
        if self.DB_TYPE == "postgres":
            return (
                f"postgresql+asyncpg://{self.POSTGRES_USER}"
                f":{self.POSTGRES_PASSWORD}"
                f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}"
                f"/{self.POSTGRES_DB}"
            )
        # sqlite — aiosqlite 异步驱动
        path = Path(self.SQLITE_PATH)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{path}"

    @property
    def redis_url(self) -> str:
        """拼接 Redis 连接 URL (仅 CACHE_TYPE=redis 时使用)。"""
        password_part = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{password_part}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例，首次调用后缓存。"""
    return Settings()


# 便捷别名 — 模块级直接引用
settings: Settings = get_settings()
