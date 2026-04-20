from __future__ import annotations

import hashlib
import json
import logging
from textwrap import dedent

from langsmith import traceable

from ..analysis import analyze_pull_request
from ..config import Settings
from ..models import ClarificationResult, DocPatch, GenerationInput
from ..patching.builder import PatchBuilder
from ..retrieval.search import retrieve_context
from ..validation.validator import PatchValidator
from .state import PRSessionState

LOGGER = logging.getLogger(__name__)


def _build_diff_summary(intent) -> str:
    hint_lines = []
    if intent.symbol_hints:
        hint_lines.append(f"Symbol hints: {', '.join(intent.symbol_hints)}")
    if intent.documentation_hints:
        hint_lines.append(f"Documentation hints: {', '.join(intent.documentation_hints)}")
    if intent.path_hints:
        hint_lines.append(f"Changed paths: {', '.join(intent.path_hints)}")
    if intent.diff_excerpt:
        hint_lines.append(f"Diff excerpt:\n{intent.diff_excerpt}")
    return "\n\n".join([intent.summary, *hint_lines]).strip()


class WorkflowNodes:
    def __init__(
        self,
        settings: Settings,
        github_client,
        llm_client,
        telegram_client=None,
        state_store=None,
    ) -> None:
        self._settings = settings
        self._github = github_client
        self._llm = llm_client
        self._telegram = telegram_client
        self._state_store = state_store
        self._patch_builder = PatchBuilder()
        self._validator = PatchValidator(settings)

    @traceable(run_type="tool", name="ingest")
    def ingest(self, state: PRSessionState) -> PRSessionState:
        event = self._github.parse_pull_request_event(state["event_payload"])
        if not event:
            return {
                "stage": "ingest",
                "outcome": "ignored",
                "error_code": "unsupported_event",
            }
        session_seed = f"{event['repo']}#{event['pr_number']}:{event['head_sha']}"
        session_id = hashlib.sha256(session_seed.encode("utf-8")).hexdigest()[:16]
        LOGGER.info("ingest", extra={"session_id": session_id, "repo": event["repo"], "pr_number": event["pr_number"]})
        if (
            event["action"] == "synchronize"
            and event.get("before_sha")
            and self._github.is_markdown_only_update(
                event["repo"],
                event["before_sha"],
                event["head_sha"],
            )
        ):
            return {
                "stage": "ingest",
                "repo": event["repo"],
                "pr_number": event["pr_number"],
                "head_sha": event["head_sha"],
                "event_action": event["action"],
                "session_id": session_id,
                "trace_id": session_id,
                "outcome": "ignored",
                "error_code": "markdown_only_update",
            }
        return {
            "stage": "ingest",
            "repo": event["repo"],
            "pr_number": event["pr_number"],
            "head_sha": event["head_sha"],
            "event_action": event["action"],
            "session_id": session_id,
            "trace_id": session_id,
            "min_confidence": self._settings.min_confidence,
        }

    @traceable(run_type="tool", name="load_pr")
    def load_pr(self, state: PRSessionState) -> PRSessionState:
        snapshot = self._github.load_pull_request(state["repo"], state["pr_number"])
        return {"stage": "load_pr", "pr_snapshot": snapshot}

    @traceable(run_type="tool", name="analyze_diff")
    def analyze_diff(self, state: PRSessionState) -> PRSessionState:
        snapshot = state["pr_snapshot"]
        intent = analyze_pull_request(snapshot, self._settings.max_diff_lines, llm_client=self._llm)
        outcome = "analysis_complete" if intent.supported else "fallback_comment"
        return {"stage": "analyze_diff", "change_intent": intent, "outcome": outcome}

    @traceable(run_type="tool", name="retrieve_docs")
    def retrieve_docs(self, state: PRSessionState) -> PRSessionState:
        snapshot = state["pr_snapshot"]
        intent = state["change_intent"]
        results = retrieve_context(
            snapshot.doc_files,
            intent,
            self._settings.max_doc_candidates,
            llm_client=self._llm,
        )
        return {"stage": "retrieve_docs", "retrieval_result": results}

    @traceable(run_type="tool", name="build_context")
    def build_context(self, state: PRSessionState) -> PRSessionState:
        snapshot = state["pr_snapshot"]
        intent = state["change_intent"]
        retrieved = state["retrieval_result"]
        pr_card = dedent(
            f"""
            Repo: {snapshot.repo}
            PR: #{snapshot.pr_number}
            Title: {snapshot.title}
            Body: {snapshot.body or "(empty)"}
            """
        ).strip()
        generation_input = GenerationInput(
            policy="Only propose safe Markdown documentation edits to allowlisted files.",
            pr_card=pr_card,
            diff_summary=_build_diff_summary(intent),
            retrieved_contexts=retrieved,
            allowed_doc_paths=[item.doc_path for item in retrieved],
        )
        return {"stage": "build_context", "generation_input": generation_input}

    @traceable(run_type="llm", name="generate")
    def generate(self, state: PRSessionState) -> PRSessionState:
        decision = self._llm.generate_decision(state["generation_input"])
        return {"stage": "generate", "llm_decision": decision}

    @traceable(run_type="tool", name="build_patch")
    def build_patch(self, state: PRSessionState) -> PRSessionState:
        snapshot = state["pr_snapshot"]
        decision = state["llm_decision"]
        patch = self._patch_builder.build(snapshot.doc_files, decision)
        return {"stage": "build_patch", "doc_patch": patch}

    @traceable(run_type="tool", name="validate")
    def validate(self, state: PRSessionState) -> PRSessionState:
        patch = state.get("doc_patch") or DocPatch(entries=[], summary="")
        report = self._validator.validate(state["pr_snapshot"], patch)
        outcome = "ready_to_publish" if report.is_valid else "fallback_comment"
        return {"stage": "validate", "validation_report": report, "outcome": outcome}

    @traceable(run_type="tool", name="publish")
    def publish(self, state: PRSessionState) -> PRSessionState:
        snapshot = state.get("pr_snapshot")
        body = self._format_comment(state)
        if snapshot is None:
            result = {
                "mode": self._settings.publish_mode,
                "published": False,
                "comment_body": body,
                "error": "missing_pr_snapshot",
            }
            return {"stage": "publish", "publish_result": result, "outcome": "ignored"}

        if self._settings.dry_run:
            mode = "commit_patch" if self._should_commit_patch(state) else "comment_only"
            publish_result = {"mode": mode, "published": False, "comment_body": body}
        elif self._should_commit_patch(state):
            patch = state["doc_patch"]
            summary = state.get("llm_decision").comment if state.get("llm_decision") else patch.summary
            publish_result = self._github.publish_patch(
                snapshot,
                patch,
                state["session_id"],
                summary,
            ).model_dump()
        else:
            publish_result = self._github.publish_comment(snapshot.repo, snapshot.pr_number, body).model_dump()
        if publish_result["mode"] == "commit_patch" and (publish_result["published"] or self._settings.dry_run):
            outcome = "patched"
        elif publish_result["comment_body"]:
            outcome = "commented"
        else:
            outcome = "failed"
        return {"stage": "publish", "publish_result": publish_result, "outcome": outcome}

    @traceable(run_type="tool", name="clarify")
    def clarify(self, state: PRSessionState) -> PRSessionState:
        snapshot = state.get("pr_snapshot")
        question = self._format_clarification_question(state)
        if snapshot is None:
            return {
                "stage": "clarify",
                "outcome": "failed",
                "error_code": "missing_pr_snapshot",
            }

        if self._settings.dry_run:
            self._save_pending_clarification(state, question)
            return {
                "stage": "clarify",
                "clarification_result": {
                    "channel": "telegram",
                    "sent": False,
                    "message": question,
                },
                "outcome": "asked_human",
            }

        if self._telegram is None:
            publish_result = self._github.publish_comment(snapshot.repo, snapshot.pr_number, question).model_dump()
            return {
                "stage": "clarify",
                "publish_result": publish_result,
                "outcome": "commented",
                "error_code": "telegram_not_configured",
            }

        self._save_pending_clarification(state, question)
        clarification_result = ClarificationResult.model_validate(
            self._telegram.send_message(question)
        ).model_dump()
        return {
            "stage": "clarify",
            "clarification_result": clarification_result,
            "outcome": "asked_human",
        }

    @traceable(run_type="tool", name="complete")
    def complete(self, state: PRSessionState) -> PRSessionState:
        return {"stage": "complete"}

    def _should_commit_patch(self, state: PRSessionState) -> bool:
        validation = state.get("validation_report")
        return bool(
            self._settings.publish_mode == "commit_patch"
            and validation
            and validation.is_valid
            and state.get("doc_patch")
        )

    def _format_comment(self, state: PRSessionState) -> str:
        intent = state.get("change_intent")
        validation = state.get("validation_report")
        patch = state.get("doc_patch")
        decision = state.get("llm_decision")

        if validation and validation.is_valid and patch:
            diff_blocks = "\n\n".join(
                f"### `{entry.doc_path}`\n```diff\n{entry.diff_preview}\n```"
                for entry in patch.entries
            )
            return dedent(
                f"""
                ## DocSync Proposal

                Scenario: {intent.scenario if intent else "unknown"}
                Summary: {decision.comment if decision else patch.summary}

                Proposed documentation patch preview:

                {diff_blocks}
                """
            ).strip()

        reasons = validation.reasons if validation else []
        fallback_reason = "; ".join(reasons) if reasons else (intent.reason if intent else "no_action")
        return dedent(
            f"""
            ## DocSync Review

            No safe documentation patch was published automatically.

            Reason: {fallback_reason}
            """
        ).strip()

    def _format_clarification_question(self, state: PRSessionState) -> str:
        snapshot = state.get("pr_snapshot")
        decision = state.get("llm_decision")
        validation = state.get("validation_report")
        intent = state.get("change_intent")
        reasons = validation.reasons if validation else []

        if decision and decision.decision == "ask_human":
            reason = decision.comment
        elif decision and decision.confidence < self._settings.min_confidence:
            reason = (
                f"Model confidence is too low ({decision.confidence:.2f} < {self._settings.min_confidence:.2f}). "
                f"{decision.comment}"
            )
        elif reasons:
            reason = "; ".join(reasons)
        else:
            reason = "The documentation update could not be safely published automatically."

        return dedent(
            f"""
            Session ID: {state.get("session_id", "unknown")}

            DocSync needs clarification for PR #{snapshot.pr_number if snapshot else "unknown"} in {snapshot.repo if snapshot else "unknown"}.
            Change scenario: {intent.scenario if intent else "unknown"}.
            Reason: {reason}

            Please confirm the intended documentation update or describe the expected behavior change.
            """
        ).strip()

    def _save_pending_clarification(self, state: PRSessionState, question: str) -> None:
        if self._state_store is None or "session_id" not in state:
            return
        self._state_store.save_pending_clarification(
            state["session_id"],
            state,
            metadata={"question": question},
        )
