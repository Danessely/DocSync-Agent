from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from .graph.state import PRSessionState


@dataclass(slots=True)
class PendingClarification:
    session_id: str
    state: PRSessionState
    metadata: dict[str, Any] = field(default_factory=dict)


class InMemorySessionStore:
    def __init__(self) -> None:
        self._pending: dict[str, PendingClarification] = {}
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
                state=state.copy(),
                metadata=dict(metadata or {}),
            )

    def get_pending_clarification(self, session_id: str) -> PendingClarification | None:
        with self._lock:
            pending = self._pending.get(session_id)
            if pending is None:
                return None
            return PendingClarification(
                session_id=pending.session_id,
                state=pending.state.copy(),
                metadata=dict(pending.metadata),
            )

    def clear_pending_clarification(self, session_id: str) -> None:
        with self._lock:
            self._pending.pop(session_id, None)
