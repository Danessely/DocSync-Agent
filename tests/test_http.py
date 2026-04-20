from __future__ import annotations

import hashlib
import hmac
import json

import httpx
import pytest

from docsync.config import Settings
from docsync.main import create_app
from docsync.models import ChangedFile, GenerationDecision, PublishResult, PullRequestSnapshot


class FakeGitHubClient:
    def __init__(self, snapshot: PullRequestSnapshot, secret: str) -> None:
        self.snapshot = snapshot
        self.secret = secret
        self.published = []

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:
        expected = "sha256=" + hmac.new(self.secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return signature == expected

    def parse_pull_request_event(self, payload):
        if payload["action"] == "closed":
            return None
        return {
            "repo": payload["repository"]["full_name"],
            "pr_number": payload["pull_request"]["number"],
            "head_sha": payload["pull_request"]["head"]["sha"],
            "action": payload["action"],
        }

    def load_pull_request(self, repo: str, pr_number: int) -> PullRequestSnapshot:
        return self.snapshot

    def publish_comment(self, repo: str, pr_number: int, body: str) -> PublishResult:
        self.published.append(body)
        return PublishResult(mode="comment_only", published=True, comment_body=body, comment_id=42)

    def publish_patch(self, snapshot: PullRequestSnapshot, patch, session_id: str, summary: str) -> PublishResult:
        return PublishResult(
            mode="commit_patch",
            published=True,
            commit_shas=["commit123"],
            committed_files=[entry.doc_path for entry in patch.entries],
            details=summary,
        )


class FakeLLMClient:
    def generate_decision(self, payload):
        return GenerationDecision(
            decision="update",
            confidence=0.9,
            comment="Update docs.",
            proposed_changes=[
                {
                    "doc_path": "README.md",
                    "section_title": "API",
                    "operation": "append",
                    "content": "- Added a timeout parameter.",
                    "rationale": "signature change",
                }
            ],
        )


def make_snapshot() -> PullRequestSnapshot:
    return PullRequestSnapshot(
        repo="acme/project",
        pr_number=9,
        title="Add timeout parameter",
        body="",
        head_sha="sha123",
        head_ref="feature/docsync",
        diff_text="""diff --git a/src/client.py b/src/client.py
@@
-def fetch_data(url):
+def fetch_data(url, timeout=30):
""",
        changed_files=[ChangedFile(path="src/client.py", patch="+def fetch_data(url, timeout=30):")],
        doc_files={"README.md": "# Project\n\n## API\n\nUse `fetch_data(url)`.\n"},
        doc_file_shas={"README.md": "sha-readme"},
    )


def sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


async def signed_json_request(client: httpx.AsyncClient, secret: str, payload: dict):
    body = json.dumps(payload).encode("utf-8")
    return await client.post(
        "/webhooks/github",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Hub-Signature-256": sign(secret, body),
        },
    )


@pytest.mark.anyio
async def test_github_webhook_accepts_valid_signature() -> None:
    secret = "topsecret"
    snapshot = make_snapshot()
    app = create_app(
        settings=Settings(github_webhook_secret=secret, dry_run=False),
        github_client=FakeGitHubClient(snapshot, secret),
        llm_client=FakeLLMClient(),
    )
    payload = {
        "action": "opened",
        "repository": {"full_name": "acme/project"},
        "pull_request": {"number": 9, "head": {"sha": "sha123"}},
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await signed_json_request(client, secret, payload)

    assert response.status_code == 200
    assert response.json()["status"] == "commented"


@pytest.mark.anyio
async def test_github_webhook_rejects_bad_signature() -> None:
    secret = "topsecret"
    snapshot = make_snapshot()
    app = create_app(
        settings=Settings(github_webhook_secret=secret, dry_run=False),
        github_client=FakeGitHubClient(snapshot, secret),
        llm_client=FakeLLMClient(),
    )
    payload = {
        "action": "opened",
        "repository": {"full_name": "acme/project"},
        "pull_request": {"number": 9, "head": {"sha": "sha123"}},
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/webhooks/github",
            content=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Hub-Signature-256": "sha256=bad"},
        )

    assert response.status_code == 401


@pytest.mark.anyio
async def test_github_webhook_ignores_unsupported_action() -> None:
    secret = "topsecret"
    snapshot = make_snapshot()
    app = create_app(
        settings=Settings(github_webhook_secret=secret, dry_run=False),
        github_client=FakeGitHubClient(snapshot, secret),
        llm_client=FakeLLMClient(),
    )
    payload = {
        "action": "closed",
        "repository": {"full_name": "acme/project"},
        "pull_request": {"number": 9, "head": {"sha": "sha123"}},
    }

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await signed_json_request(client, secret, payload)

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
