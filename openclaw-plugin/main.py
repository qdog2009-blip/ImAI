"""
OpenClaw Channel Plugin — 入口。

运行方式:
    python main.py

停止方式:
    Ctrl+C  (发送 SIGINT 信号)
"""

from __future__ import annotations

import asyncio
import logging
import signal

from config import settings
from plugin import OpenClawPlugin

logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

logger = logging.getLogger(__name__)


async def _main() -> None:
    plugin = OpenClawPlugin()
    loop = asyncio.get_running_loop()

    # 优雅停止: Ctrl+C / SIGTERM
    def _shutdown(*_):
        logger.info("收到停止信号，正在关闭…")
        asyncio.ensure_future(plugin.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    await plugin.run()
    logger.info("OpenClaw Channel Plugin 已停止。")


if __name__ == "__main__":
    asyncio.run(_main())
