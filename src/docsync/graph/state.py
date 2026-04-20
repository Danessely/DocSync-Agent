from __future__ import annotations

from typing import Any, TypedDict

from ..models import (
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


class PRSessionState(TypedDict, total=False):
    event_payload: dict[str, Any]
    event_action: str
    session_id: str
    trace_id: str
    repo: str
    pr_number: int
    head_sha: str
    stage: str
    pr_snapshot: PullRequestSnapshot
    change_intent: ChangeIntent
    retrieval_result: list[RetrievedContext]
    generation_input: GenerationInput
    llm_decision: GenerationDecision
    doc_patch: DocPatch
    validation_report: ValidationReport
    publish_result: PublishResult
    clarification_result: ClarificationResult
    outcome: str
    error_code: str
