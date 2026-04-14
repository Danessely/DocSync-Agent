from __future__ import annotations

import json
from typing import Any, Protocol

import httpx
from pydantic import ValidationError

from ..config import Settings
from ..models import GenerationDecision, GenerationInput
from ..prompts.generate import build_messages


class LLMError(RuntimeError):
    """Raised for LLM provider failures."""


class LLMClient(Protocol):
    def generate_decision(self, payload: GenerationInput) -> GenerationDecision: ...


def _strip_code_fences(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


class OpenAICompatibleLLMClient:
    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = httpx.Client(
            base_url=settings.llm_api_base_url.rstrip("/"),
            timeout=settings.llm_timeout_sec,
            headers={
                "Authorization": f"Bearer {settings.llm_api_key}" if settings.llm_api_key else "",
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    def generate_decision(self, payload: GenerationInput) -> GenerationDecision:
        payload = GenerationInput.model_validate(payload)
        messages = build_messages(payload)
        content = self._request_completion(messages)
        try:
            return GenerationDecision.model_validate_json(_strip_code_fences(content))
        except ValidationError:
            repair_messages = messages + [
                {
                    "role": "user",
                    "content": "Your previous response did not match the required JSON schema. Return valid JSON only.",
                }
            ]
            repaired = self._request_completion(repair_messages)
            try:
                return GenerationDecision.model_validate_json(_strip_code_fences(repaired))
            except ValidationError as exc:
                raise LLMError("invalid_schema") from exc

    def _request_completion(self, messages: list[dict[str, str]]) -> str:
        response = self._client.post(
            "/chat/completions",
            json={
                "model": self._settings.llm_model,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
                "messages": messages,
            },
        )
        if response.status_code >= 400:
            raise LLMError(f"provider_error_{response.status_code}")
        payload = response.json()
        try:
            return payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError("invalid_provider_payload") from exc


class MockLLMClient:
    def generate_decision(self, payload: GenerationInput) -> GenerationDecision:
        if not payload.retrieved_contexts:
            return GenerationDecision(
                decision="skip",
                confidence=0.2,
                comment="No relevant documentation context was available.",
                proposed_changes=[],
            )

        primary = payload.retrieved_contexts[0]
        hint = primary.section_title or "Documentation"
        return GenerationDecision(
            decision="update",
            confidence=0.75,
            comment="Mock provider generated a minimal documentation update.",
            proposed_changes=[
                {
                    "doc_path": primary.doc_path,
                    "section_title": hint,
                    "operation": "append",
                    "content": "- Update this section to reflect the pull request change.\n",
                    "rationale": "Mock change for local execution.",
                }
            ],
        )
