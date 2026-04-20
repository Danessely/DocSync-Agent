from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from pydantic import BaseModel

from .graph.state import PRSessionState
from .models import (
    ChangeIntent,
    ClarificationResult,
    DocPatch,
    GenerationDecision,
    GenerationInput,
    PublishResult,
    PullRequestSnapshot,
    RetrievedContext,
    ValidationReport,
)


@dataclass(slots=True)
class PendingClarification:
    session_id: str
    state: PRSessionState
    metadata: dict[str, Any] = field(default_factory=dict)


class SessionStore(Protocol):
    def save_pending_clarification(
        self,
        session_id: str,
        state: PRSessionState,
        metadata: dict[str, Any] | None = None,
    ) -> None: ...
    def get_pending_clarification(self, session_id: str) -> PendingClarification | None: ...
    def clear_pending_clarification(self, session_id: str) -> None: ...
    def mark_processed_head(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        outcome: str,
        session_id: str,
    ) -> None: ...
    def get_processed_head(self, repo: str, pr_number: int, head_sha: str) -> dict[str, Any] | None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingClarification] = {}
        self._processed_heads: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def save_pending_clarification(
        self,
        session_id: str,
        state: PRSessionState,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._pending[session_id] = PendingClarification(
                session_id=session_id,
                state=_clone_state(state),
                metadata=dict(metadata or {}),
            )

    def get_pending_clarification(self, session_id: str) -> PendingClarification | None:
        with self._lock:
            pending = self._pending.get(session_id)
            if pending is None:
                return None
            return PendingClarification(
                session_id=pending.session_id,
                state=_clone_state(pending.state),
                metadata=dict(pending.metadata),
            )

    def clear_pending_clarification(self, session_id: str) -> None:
        with self._lock:
            self._pending.pop(session_id, None)

    def mark_processed_head(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        outcome: str,
        session_id: str,
    ) -> None:
        with self._lock:
            self._processed_heads[_head_key(repo, pr_number, head_sha)] = {
                "repo": repo,
                "pr_number": pr_number,
                "head_sha": head_sha,
                "outcome": outcome,
                "session_id": session_id,
            }

    def get_processed_head(self, repo: str, pr_number: int, head_sha: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._processed_heads.get(_head_key(repo, pr_number, head_sha))
            if record is None:
                return None
            return dict(record)


class FileSessionStore(InMemorySessionStore):
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file_lock = Lock()
        super().__init__()
        self._load()

    def save_pending_clarification(
        self,
        session_id: str,
        state: PRSessionState,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        super().save_pending_clarification(session_id, state, metadata)
        self._persist()

    def clear_pending_clarification(self, session_id: str) -> None:
        super().clear_pending_clarification(session_id)
        self._persist()

    def mark_processed_head(
        self,
        repo: str,
        pr_number: int,
        head_sha: str,
        outcome: str,
        session_id: str,
    ) -> None:
        super().mark_processed_head(repo, pr_number, head_sha, outcome, session_id)
        self._persist()

    def _load(self) -> None:
        if not self._path.exists():
            return
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        pending_items = payload.get("pending", {})
        processed_heads = payload.get("processed_heads", {})
        self._pending = {
            session_id: PendingClarification(
                session_id=session_id,
                state=_deserialize_state(item.get("state", {})),
                metadata=dict(item.get("metadata") or {}),
            )
            for session_id, item in pending_items.items()
        }
        self._processed_heads = {
            key: dict(value)
            for key, value in processed_heads.items()
            if isinstance(value, dict)
        }

    def _persist(self) -> None:
        payload = {
            "pending": {
                session_id: {
                    "state": _serialize_state(pending.state),
                    "metadata": pending.metadata,
                }
                for session_id, pending in self._pending.items()
            },
            "processed_heads": self._processed_heads,
        }
        with self._file_lock:
            tmp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
            tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            tmp_path.replace(self._path)


def _head_key(repo: str, pr_number: int, head_sha: str) -> str:
    return f"{repo}#{pr_number}:{head_sha}"


def _clone_state(state: PRSessionState) -> PRSessionState:
    return _deserialize_state(_serialize_state(state))


def _serialize_state(state: PRSessionState) -> dict[str, Any]:
    serialized: dict[str, Any] = {}
    for key, value in state.items():
        if isinstance(value, BaseModel):
            serialized[key] = value.model_dump(mode="json")
        elif isinstance(value, list):
            serialized[key] = [
                item.model_dump(mode="json") if isinstance(item, BaseModel) else item
                for item in value
            ]
        else:
            serialized[key] = value
    return serialized


def _deserialize_state(payload: dict[str, Any]) -> PRSessionState:
    state: PRSessionState = dict(payload)
    scalar_models: dict[str, type[BaseModel]] = {
        "pr_snapshot": PullRequestSnapshot,
        "change_intent": ChangeIntent,
        "generation_input": GenerationInput,
        "llm_decision": GenerationDecision,
        "doc_patch": DocPatch,
        "validation_report": ValidationReport,
        "publish_result": PublishResult,
        "clarification_result": ClarificationResult,
    }
    list_models: dict[str, type[BaseModel]] = {
        "retrieval_result": RetrievedContext,
    }
    for key, model in scalar_models.items():
        if key in state and isinstance(state[key], dict):
            state[key] = model.model_validate(state[key])  # type: ignore[assignment]
    for key, model in list_models.items():
        if key in state and isinstance(state[key], list):
            state[key] = [model.model_validate(item) for item in state[key]]  # type: ignore[assignment]
    return state
