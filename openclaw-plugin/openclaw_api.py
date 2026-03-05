"""
OpenClaw API 客户端 — 调用 OpenClaw 对话接口获取 AI 回复。

兼容 OpenAI Chat Completions API 格式 (POST /chat/completions)。
OpenClaw 的接口遵循同一规范，切换模型只需修改 .env 中的配置项。
"""

from __future__ import annotations

import logging
from typing import Optional

import aiohttp

from config import settings

logger = logging.getLogger(__name__)

# 每个用户的对话历史 (内存，进程重启后清空)
# key: sender_id, value: list of {"role": ..., "content": ...}
_conversation_history: dict[str, list[dict]] = {}

# 单个用户最多保留的历史轮数
_MAX_HISTORY_TURNS = 20


class OpenClawAPI:
    """
    OpenClaw 对话接口封装。

    - 每个用户独立维护对话历史 (多轮对话)
    - 超出历史上限时滑动窗口截断，保留最近的对话
    """

    def __init__(self) -> None:
        self._base_url = settings.OPENCLAW_API_BASE_URL.rstrip("/")
        self._api_key = settings.OPENCLAW_API_KEY
        self._model = settings.OPENCLAW_MODEL
        self._timeout = aiohttp.ClientTimeout(total=settings.OPENCLAW_TIMEOUT)
        self._system_prompt = settings.OPENCLAW_SYSTEM_PROMPT

    async def chat(self, user_id: str, user_message: str) -> str:
        """
        发送用户消息，返回 AI 回复文本。

        :param user_id:      ImAI 用户 ID (用于维护独立会话历史)
        :param user_message: 用户输入的文本
        :returns:            AI 回复文本
        :raises RuntimeError: API 调用失败时抛出
        """
        history = _conversation_history.setdefault(user_id, [])

        # 追加用户消息
        history.append({"role": "user", "content": user_message})

        # 构建消息列表: system prompt + 历史对话
        messages = []
        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})
        messages.extend(history)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": messages,
        }

        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise RuntimeError(
                            f"OpenClaw API 错误 [{resp.status}]: {body}"
                        )
                    data = await resp.json()

            reply = data["choices"][0]["message"]["content"]

        except aiohttp.ClientError as exc:
            # 网络错误：从历史中移除刚追加的用户消息，避免污染下一轮
            history.pop()
            raise RuntimeError(f"OpenClaw 网络请求失败: {exc}") from exc

        # 追加 AI 回复到历史
        history.append({"role": "assistant", "content": reply})

        # 滑动窗口截断：保留最新的 N 轮 (每轮 = user + assistant 各一条)
        max_msgs = _MAX_HISTORY_TURNS * 2
        if len(history) > max_msgs:
            _conversation_history[user_id] = history[-max_msgs:]

        logger.debug(
            "openclaw: user=%s reply=%.80s history_len=%d",
            user_id, reply, len(_conversation_history[user_id]),
        )
        return reply

    def clear_history(self, user_id: str) -> None:
        """清除指定用户的对话历史。"""
        _conversation_history.pop(user_id, None)
        logger.info("openclaw: 已清除用户 %s 的对话历史", user_id)
