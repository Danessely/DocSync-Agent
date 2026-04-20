from __future__ import annotations

from typing import Protocol

import httpx

from ..models import ClarificationResult


class TelegramError(RuntimeError):
    """Raised for Telegram API failures."""


class TelegramClient(Protocol):
    def send_message(self, text: str) -> ClarificationResult: ...


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
