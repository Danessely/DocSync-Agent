from __future__ import annotations

from docsync.models import ChangeIntent
from docsync.state_store import FileSessionStore


def test_file_session_store_persists_pending_clarifications(tmp_path) -> None:
    path = tmp_path / "session_store.json"
    store = FileSessionStore(path)
    state = {
        "session_id": "abc123",
        "repo": "acme/project",
        "pr_number": 7,
        "head_sha": "head123",
        "change_intent": ChangeIntent(
            supported=True,
            scenario="behavior_change",
            confidence=0.9,
            summary="Behavior changed.",
            reason="llm_analysis",
            diff_excerpt="diff",
            symbol_hints=["fetch_data"],
            path_hints=["src/client.py"],
            documentation_hints=["behavior"],
        ),
        "outcome": "asked_human",
    }
    store.save_pending_clarification("abc123", state, metadata={"question": "Please clarify."})

    reopened = FileSessionStore(path)
    pending = reopened.get_pending_clarification("abc123")

    assert pending is not None
    assert pending.metadata["question"] == "Please clarify."
    assert pending.state["change_intent"].scenario == "behavior_change"


def test_file_session_store_persists_processed_heads(tmp_path) -> None:
    path = tmp_path / "session_store.json"
    store = FileSessionStore(path)
    store.mark_processed_head("acme/project", 7, "head123", "commented", "abc123")

    reopened = FileSessionStore(path)
    processed = reopened.get_processed_head("acme/project", 7, "head123")

    assert processed == {
        "repo": "acme/project",
        "pr_number": 7,
        "head_sha": "head123",
        "outcome": "commented",
        "session_id": "abc123",
    }
