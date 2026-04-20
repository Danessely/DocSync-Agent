from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .adapters.llm import ChatOpenAILLMClient, MockLLMClient
from .adapters.telegram import TelegramBotClient
from .config import Settings
from .graph.workflow import DocSyncWorkflow
from .models import PublishResult, PullRequestSnapshot
from .state_store import InMemorySessionStore


class SnapshotBundle(BaseModel):
    event_payload: dict[str, Any]
    pr_snapshot: PullRequestSnapshot


class SnapshotGitHubClient:
    def __init__(self, snapshot: PullRequestSnapshot) -> None:
        self._snapshot = snapshot
        self.published_comments: list[str] = []
        self.published_patches: list[dict[str, object]] = []

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:
        return True

    def parse_pull_request_event(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        action = payload.get("action")
        if action not in {"opened", "reopened", "synchronize"}:
            return None
        pr = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name")
        if not repo or not pr.get("number"):
            return None
        return {
            "repo": repo,
            "pr_number": int(pr["number"]),
            "head_sha": ((pr.get("head") or {}).get("sha") or ""),
            "before_sha": payload.get("before") or "",
            "action": action,
        }

    def is_markdown_only_update(self, repo: str, before_sha: str, head_sha: str) -> bool:
        del repo, before_sha, head_sha
        return False

    def load_pull_request(self, repo: str, pr_number: int) -> PullRequestSnapshot:
        if repo != self._snapshot.repo or pr_number != self._snapshot.pr_number:
            raise ValueError("Snapshot payload does not match the requested pull request.")
        return self._snapshot

    def publish_comment(self, repo: str, pr_number: int, body: str) -> PublishResult:
        self.published_comments.append(body)
        return PublishResult(mode="comment_only", published=True, comment_body=body, comment_id=1)

    def publish_patch(self, snapshot: PullRequestSnapshot, patch, session_id: str, summary: str) -> PublishResult:
        self.published_patches.append(
            {
                "session_id": session_id,
                "summary": summary,
                "files": [entry.doc_path for entry in patch.entries],
            }
        )
        return PublishResult(
            mode="commit_patch",
            published=True,
            commit_shas=["snapshot-commit"],
            committed_files=[entry.doc_path for entry in patch.entries],
            details=summary,
        )


def load_snapshot_bundle(path: str | Path) -> SnapshotBundle:
    return SnapshotBundle.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _build_llm_client(settings: Settings):
    if settings.llm_provider == "mock":
        return MockLLMClient()
    return ChatOpenAILLMClient(settings)


def _build_telegram_client(settings: Settings):
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return None
    return TelegramBotClient(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        timeout=settings.telegram_timeout_sec,
    )


def run_snapshot(
    snapshot_path: str | Path,
    settings: Settings | None = None,
    llm_client=None,
) -> tuple[dict[str, Any], list[str]]:
    bundle = load_snapshot_bundle(snapshot_path)
    result, github_client = run_snapshot_bundle(
        bundle,
        settings=settings,
        llm_client=llm_client,
    )
    return result, github_client.published_comments


def run_snapshot_bundle(
    bundle: SnapshotBundle,
    settings: Settings | None = None,
    llm_client=None,
) -> tuple[dict[str, Any], SnapshotGitHubClient]:
    settings = settings or Settings.from_env()
    github_client = SnapshotGitHubClient(bundle.pr_snapshot)
    llm_client = llm_client or _build_llm_client(settings)
    telegram_client = _build_telegram_client(settings)
    workflow = DocSyncWorkflow(
        settings,
        github_client,
        llm_client,
        telegram_client=telegram_client,
        state_store=InMemorySessionStore(),
    )
    result = workflow.run_once(bundle.event_payload)
    return result, github_client


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run DocSync against a saved pull request snapshot.")
    parser.add_argument("snapshot", help="Path to a JSON snapshot bundle.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the result as JSON.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    result, comments = run_snapshot(args.snapshot)

    if args.json:
        payload = {
            "status": result.get("outcome"),
            "stage": result.get("stage"),
            "comment_count": len(comments),
            "comments": comments,
            "session_id": result.get("session_id"),
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"status: {result.get('outcome')}")
    print(f"stage: {result.get('stage')}")
    print(f"session_id: {result.get('session_id')}")
    if comments:
        print("\ncomment:\n")
        print(comments[0])
    else:
        print("\ncomment:\n(none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
