from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .adapters.llm import MockLLMClient, OpenAICompatibleLLMClient
from .config import Settings
from .graph.workflow import DocSyncWorkflow
from .models import PublishResult, PullRequestSnapshot


class SnapshotBundle(BaseModel):
    event_payload: dict[str, Any]
    pr_snapshot: PullRequestSnapshot


class SnapshotGitHubClient:
    def __init__(self, snapshot: PullRequestSnapshot) -> None:
        self._snapshot = snapshot
        self.published_comments: list[str] = []

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
            "action": action,
        }

    def load_pull_request(self, repo: str, pr_number: int) -> PullRequestSnapshot:
        if repo != self._snapshot.repo or pr_number != self._snapshot.pr_number:
            raise ValueError("Snapshot payload does not match the requested pull request.")
        return self._snapshot

    def publish_comment(self, repo: str, pr_number: int, body: str) -> PublishResult:
        self.published_comments.append(body)
        return PublishResult(mode="comment_only", published=True, comment_body=body, comment_id=1)


def load_snapshot_bundle(path: str | Path) -> SnapshotBundle:
    return SnapshotBundle.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _build_llm_client(settings: Settings):
    if settings.llm_provider == "mock":
        return MockLLMClient()
    return OpenAICompatibleLLMClient(settings)


def run_snapshot(
    snapshot_path: str | Path,
    settings: Settings | None = None,
    llm_client=None,
) -> tuple[dict[str, Any], list[str]]:
    settings = settings or Settings.from_env()
    bundle = load_snapshot_bundle(snapshot_path)
    github_client = SnapshotGitHubClient(bundle.pr_snapshot)
    llm_client = llm_client or _build_llm_client(settings)
    workflow = DocSyncWorkflow(settings, github_client, llm_client)
    result = workflow.run_once(bundle.event_payload)
    return result, github_client.published_comments


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
