from __future__ import annotations

from .state import PRSessionState


def route_after_ingest(state: PRSessionState) -> str:
    return "load_pr" if state.get("repo") and state.get("pr_number") else "complete"


def route_after_analyze(state: PRSessionState) -> str:
    intent = state["change_intent"]
    return "retrieve_docs" if intent.supported else "publish"


def route_after_retrieve(state: PRSessionState) -> str:
    return "build_context" if state.get("retrieval_result") else "publish"


def route_after_generate(state: PRSessionState) -> str:
    decision = state["llm_decision"]
    min_confidence = state.get("min_confidence", 0.0)
    if decision.decision == "ask_human":
        return "clarify"
    if decision.decision == "update" and decision.confidence < min_confidence:
        return "clarify"
    if decision.decision == "update" and decision.proposed_changes:
        return "build_patch"
    return "publish"


def route_after_validate(state: PRSessionState) -> str:
    return "publish" if state["validation_report"].is_valid else "clarify"
