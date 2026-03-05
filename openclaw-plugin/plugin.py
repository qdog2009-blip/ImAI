"""
OpenClaw Channel Plugin — 核心编排层。

职责:
  - 收到 ImAI 消息 → 调用 OpenClaw API → 将回复发回给发送方
  - 支持特殊指令 (/clear 清除对话历史)
  - 过滤掉插件自身发出的消息，防止循环
"""

from __future__ import annotations

import logging

from imai_client import ImAIClient, IncomingMessage
from openclaw_api import OpenClawAPI
from proto_helper import ContentType

logger = logging.getLogger(__name__)

# 触发对话历史清除的指令
_CLEAR_CMD = "/clear"


class OpenClawPlugin:
    """
    OpenClaw Channel Plugin 主逻辑。

    用法::

        plugin = OpenClawPlugin()
        await plugin.run()           # 阻塞运行，含自动重连
    """

    def __init__(self) -> None:
        self._openclaw = OpenClawAPI()
        self._client = ImAIClient(on_message=self._handle_message)

    async def run(self) -> None:
        """启动插件，阻塞直到收到停止信号。"""
        logger.info("OpenClaw Channel Plugin 启动中…")
        await self._client.run()

    async def stop(self) -> None:
        await self._client.stop()

    # ------------------------------------------------------------------ #
    #  消息处理
    # ------------------------------------------------------------------ #

    async def _handle_message(self, msg: IncomingMessage) -> None:
        """
        收到 ChatMessage 时的回调入口。

        只处理:
          - 私聊文本消息
          - 且发送方不是插件自身 (防止循环)
        """
        # 忽略非文本消息
        if msg.content_type != ContentType.TEXT:
            logger.debug(
                "plugin: 忽略非文本消息 content_type=%d sender=%s",
                msg.content_type, msg.sender_id,
            )
            return

        # 忽略插件自身发出的消息
        if msg.sender_id == self._client._user_id:
            return

        sender_id = msg.sender_id
        text = msg.text.strip()

        if not text:
            return

        logger.info("plugin: 处理消息 from=%s text=%.80s", sender_id, text)

        # 特殊指令: /clear
        if text.lower() == _CLEAR_CMD:
            self._openclaw.clear_history(sender_id)
            await self._client.send_text(
                receiver_id=sender_id,
                text="✅ 对话历史已清除，让我们重新开始吧！",
            )
            return

        # 调用 OpenClaw API 获取回复
        try:
            reply = await self._openclaw.chat(
                user_id=sender_id,
                user_message=text,
            )
        except RuntimeError as exc:
            logger.error("plugin: OpenClaw API 调用失败: %s", exc)
            await self._client.send_text(
                receiver_id=sender_id,
                text="⚠️ AI 服务暂时不可用，请稍后再试。",
            )
            return

        # 将回复发回给发送方
        await self._client.send_text(receiver_id=sender_id, text=reply)
        logger.info(
            "plugin: 已回复 → %s reply=%.80s", sender_id, reply
        )
