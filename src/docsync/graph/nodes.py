from __future__ import annotations

import hashlib
import json
import logging
from textwrap import dedent

from ..analysis import analyze_pull_request
from ..config import Settings
from ..models import DocPatch, GenerationInput
from ..patching.builder import PatchBuilder
from ..retrieval.search import retrieve_context
from ..validation.validator import PatchValidator
from .state import PRSessionState

LOGGER = logging.getLogger(__name__)


class WorkflowNodes:
    def __init__(
        self,
        settings: Settings,
        github_client,
        llm_client,
    ) -> None:
        self._settings = settings
        self._github = github_client
        self._llm = llm_client
        self._patch_builder = PatchBuilder()
        self._validator = PatchValidator(settings)

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
        return {
            "stage": "ingest",
            "repo": event["repo"],
            "pr_number": event["pr_number"],
            "head_sha": event["head_sha"],
            "event_action": event["action"],
            "session_id": session_id,
            "trace_id": session_id,
        }

    def load_pr(self, state: PRSessionState) -> PRSessionState:
        snapshot = self._github.load_pull_request(state["repo"], state["pr_number"])
        return {"stage": "load_pr", "pr_snapshot": snapshot}

    def analyze_diff(self, state: PRSessionState) -> PRSessionState:
        snapshot = state["pr_snapshot"]
        intent = analyze_pull_request(snapshot, self._settings.max_diff_lines)
        outcome = "analysis_complete" if intent.supported else "fallback_comment"
        return {"stage": "analyze_diff", "change_intent": intent, "outcome": outcome}

    def retrieve_docs(self, state: PRSessionState) -> PRSessionState:
        snapshot = state["pr_snapshot"]
        intent = state["change_intent"]
        results = retrieve_context(snapshot.doc_files, intent, self._settings.max_doc_candidates)
        return {"stage": "retrieve_docs", "retrieval_result": results}

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
            diff_summary=intent.summary,
            retrieved_contexts=retrieved,
            allowed_doc_paths=[item.doc_path for item in retrieved],
        )
        return {"stage": "build_context", "generation_input": generation_input}

    def generate(self, state: PRSessionState) -> PRSessionState:
        decision = self._llm.generate_decision(state["generation_input"])
        return {"stage": "generate", "llm_decision": decision}

    def build_patch(self, state: PRSessionState) -> PRSessionState:
        snapshot = state["pr_snapshot"]
        decision = state["llm_decision"]
        patch = self._patch_builder.build(snapshot.doc_files, decision)
        return {"stage": "build_patch", "doc_patch": patch}

    def validate(self, state: PRSessionState) -> PRSessionState:
        patch = state.get("doc_patch") or DocPatch(entries=[], summary="")
        report = self._validator.validate(state["pr_snapshot"], patch)
        outcome = "ready_to_publish" if report.is_valid else "fallback_comment"
        return {"stage": "validate", "validation_report": report, "outcome": outcome}

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
            publish_result = {
                "mode": self._settings.publish_mode,
                "published": False,
                "comment_body": body,
            }
        else:
            publish_result = self._github.publish_comment(snapshot.repo, snapshot.pr_number, body).model_dump()
        outcome = "commented" if publish_result["comment_body"] else "failed"
        return {"stage": "publish", "publish_result": publish_result, "outcome": outcome}

    def complete(self, state: PRSessionState) -> PRSessionState:
        return {"stage": "complete"}

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

