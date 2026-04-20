from __future__ import annotations

from typing import Any, Protocol

from langchain_openai import ChatOpenAI
from pydantic import ValidationError

from ..config import Settings
from ..models import (
    ChangeAnalysis,
    ChangeIntent,
    GenerationDecision,
    GenerationInput,
    PullRequestSnapshot,
    RetrievedContext,
    RetrievedContextSelectionResult,
)
from ..prompts.analyze import build_messages as build_analysis_messages
from ..prompts.generate import build_messages
from ..prompts.retrieve import build_messages as build_retrieval_messages


class LLMError(RuntimeError):
    """Raised for LLM provider failures."""


class LLMClient(Protocol):
    def analyze_change(self, snapshot: PullRequestSnapshot) -> ChangeIntent: ...
    def select_retrieved_contexts(
        self,
        intent: ChangeIntent,
        candidates: list[RetrievedContext],
        max_candidates: int,
    ) -> list[RetrievedContext]: ...
    def generate_decision(self, payload: GenerationInput) -> GenerationDecision: ...


def _strip_code_fences(content: str) -> str:
    stripped = content.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return stripped


class ChatOpenAILLMClient:
    def __init__(
        self,
        settings: Settings,
        chat_model: Any | None = None,
    ) -> None:
        self._settings = settings
        self._chat_model = chat_model or ChatOpenAI(
            model=settings.llm_model,
            temperature=0.1,
            timeout=settings.llm_timeout_sec,
            max_retries=0,
            api_key=settings.llm_api_key or None,
            base_url=settings.llm_api_base_url or None,
            model_kwargs={"response_format": {"type": "json_object"}},
        )

    def analyze_change(self, snapshot: PullRequestSnapshot) -> ChangeIntent:
        snapshot = PullRequestSnapshot.model_validate(snapshot)
        response = self._request_model(
            build_analysis_messages(snapshot),
            ChangeAnalysis,
        )
        return ChangeIntent(
            supported=response.supported,
            scenario=response.scenario,
            confidence=response.confidence,
            summary=response.summary,
            reason=response.reason,
            diff_excerpt="",
            symbol_hints=response.symbol_hints,
            path_hints=response.path_hints,
            documentation_hints=response.documentation_hints,
        )

    def select_retrieved_contexts(
        self,
        intent: ChangeIntent,
        candidates: list[RetrievedContext],
        max_candidates: int,
    ) -> list[RetrievedContext]:
        if not candidates:
            return []

        result = self._request_model(
            build_retrieval_messages(intent, candidates, max_candidates),
            RetrievedContextSelectionResult,
        )
        lookup = {(item.doc_path, item.section_title): item for item in candidates}
        selected: list[RetrievedContext] = []
        seen: set[tuple[str, str]] = set()
        for item in result.selections:
            key = (item.doc_path, item.section_title)
            candidate = lookup.get(key)
            if candidate is None or key in seen:
                continue
            seen.add(key)
            selected.append(
                candidate.model_copy(
                    update={
                        "score": item.score,
                        "selection_reason": item.selection_reason,
                    }
                )
            )
            if len(selected) >= max_candidates:
                break
        return selected

    def generate_decision(self, payload: GenerationInput) -> GenerationDecision:
        payload = GenerationInput.model_validate(payload)
        return self._request_model(build_messages(payload), GenerationDecision)

    def _request_model(self, messages: list[dict[str, str]], response_model: type[Any]) -> Any:
        content = self._request_completion(messages)
        try:
            return response_model.model_validate_json(_strip_code_fences(content))
        except ValidationError:
            repair_messages = messages + [
                {
                    "role": "user",
                    "content": (
                        "Your previous response did not match the required JSON schema. "
                        "Return valid JSON only."
                    ),
                }
            ]
            repaired = self._request_completion(repair_messages)
            try:
                return response_model.model_validate_json(_strip_code_fences(repaired))
            except ValidationError as exc:
                raise LLMError("invalid_schema") from exc

    def _request_completion(self, messages: list[dict[str, str]]) -> str:
        try:
            response = self._chat_model.invoke(messages)
        except Exception as exc:
            raise LLMError("provider_error") from exc

        try:
            return _extract_content_text(response)
        except (AttributeError, TypeError, ValueError) as exc:
            raise LLMError("invalid_provider_payload") from exc


def _extract_content_text(message: Any) -> str:
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
        flattened = "\n".join(part for part in parts if part).strip()
        if flattened:
            return flattened
    raise ValueError("Response did not include string content.")


class MockLLMClient:
    def analyze_change(self, snapshot: PullRequestSnapshot) -> ChangeIntent:
        changed_paths = [item.path for item in snapshot.changed_files]
        source_paths = [path for path in changed_paths if not path.endswith(".md")]
        return ChangeIntent(
            supported=bool(source_paths),
            scenario="mock_analysis" if source_paths else "docs_only",
            confidence=0.65 if source_paths else 0.95,
            summary="Mock analysis detected code changes that may require docs updates."
            if source_paths
            else "PR changes documentation only.",
            reason="mock_analysis",
            diff_excerpt="",
            symbol_hints=[],
            path_hints=changed_paths,
            documentation_hints=["usage", "configuration"] if source_paths else [],
        )

    def select_retrieved_contexts(
        self,
        intent: ChangeIntent,
        candidates: list[RetrievedContext],
        max_candidates: int,
    ) -> list[RetrievedContext]:
        del intent
        return candidates[:max_candidates]

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
