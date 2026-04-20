from __future__ import annotations

import re
from typing import Protocol

import httpx

from ..models import ClarificationResult, TelegramReply


class TelegramError(RuntimeError):
    """Raised for Telegram API failures."""


class TelegramClient(Protocol):
    def send_message(self, text: str) -> ClarificationResult: ...
    def parse_reply(self, payload: dict) -> TelegramReply | None: ...


class TelegramBotClient:
    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._client = httpx.Client(
            base_url=f"https://api.telegram.org/bot{bot_token}",
            timeout=timeout,
            transport=transport,
        )

    def send_message(self, text: str) -> ClarificationResult:
        response = self._client.post(
            "/sendMessage",
            json={"chat_id": self._chat_id, "text": text},
        )
        if response.status_code >= 400:
            raise TelegramError(f"telegram_http_{response.status_code}: {response.text}")
        return ClarificationResult(channel="telegram", sent=True, message=text)

    def parse_reply(self, payload: dict) -> TelegramReply | None:
        message = payload.get("message") or payload.get("edited_message")
        if not isinstance(message, dict):
            return None
        text = message.get("text")
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if not text or chat_id is None:
            return None
        reply_to_text = ((message.get("reply_to_message") or {}).get("text")) or ""
        return TelegramReply(
            chat_id=str(chat_id),
            text=text,
            message_id=message.get("message_id"),
            reply_to_text=reply_to_text,
        )


SESSION_ID_RE = re.compile(r"Session ID:\s*([a-f0-9]{16})", re.IGNORECASE)


def extract_session_id(reply: TelegramReply) -> str | None:
    for text in (reply.text, reply.reply_to_text):
        match = SESSION_ID_RE.search(text or "")
        if match:
            return match.group(1)
    return None
