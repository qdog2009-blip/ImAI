"""
OpenClaw Channel Plugin — 配置加载。

从 .env 文件读取所有配置项，严禁在代码中硬编码敏感信息。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

_PLUGIN_DIR = Path(__file__).resolve().parent


class PluginSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_PLUGIN_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -------------------- ImAI 服务器 --------------------
    IMAI_SERVER_URL: str = "http://localhost:8000"
    IMAI_SERVER_SECRET_KEY: str
    IMAI_USERNAME: str
    IMAI_PASSWORD: str
    IMAI_DEVICE_ID: str = "openclaw-plugin-001"

    # -------------------- OpenClaw API --------------------
    OPENCLAW_API_BASE_URL: str
    OPENCLAW_API_KEY: str
    OPENCLAW_MODEL: str = "openclaw-chat"
    OPENCLAW_TIMEOUT: int = 60
    OPENCLAW_SYSTEM_PROMPT: str = "你是一个智能助手，请用简洁、友好的方式回答用户的问题。"

    # -------------------- 插件行为 --------------------
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    RECONNECT_INTERVAL: int = 5
    MAX_RECONNECT_ATTEMPTS: int = 0

    # -------------------- 派生属性 --------------------

    @property
    def ws_url_base(self) -> str:
        """将 http(s):// 转换为 ws(s)://"""
        url = self.IMAI_SERVER_URL
        if url.startswith("https://"):
            return "wss://" + url[len("https://"):]
        if url.startswith("http://"):
            return "ws://" + url[len("http://"):]
        return url


settings = PluginSettings()
