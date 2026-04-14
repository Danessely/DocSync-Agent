from __future__ import annotations

import hashlib

import httpx

from docsync.adapters.llm import OpenAICompatibleLLMClient
from docsync.config import Settings
from docsync.graph.workflow import DocSyncWorkflow
from docsync.models import ChangedFile, GenerationDecision, PublishResult, PullRequestSnapshot
from docsync.retrieval.search import retrieve_context


class FakeGitHubClient:
    def __init__(self, snapshot: PullRequestSnapshot) -> None:
        self.snapshot = snapshot
        self.published_bodies: list[str] = []

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:
        return True

    def parse_pull_request_event(self, payload):
        pr = payload["pull_request"]
        return {
            "repo": payload["repository"]["full_name"],
            "pr_number": pr["number"],
            "head_sha": pr["head"]["sha"],
            "action": payload["action"],
        }

    def load_pull_request(self, repo: str, pr_number: int) -> PullRequestSnapshot:
        assert repo == self.snapshot.repo
        assert pr_number == self.snapshot.pr_number
        return self.snapshot

    def publish_comment(self, repo: str, pr_number: int, body: str) -> PublishResult:
        self.published_bodies.append(body)
        return PublishResult(mode="comment_only", published=True, comment_body=body, comment_id=1)


class StubLLMClient:
    def __init__(self, decision: GenerationDecision) -> None:
        self.decision = decision
        self.calls = 0

    def generate_decision(self, payload):
        self.calls += 1
        return self.decision


def make_settings(**overrides) -> Settings:
    base = Settings(
        github_webhook_secret="secret",
        dry_run=False,
        max_diff_lines=1000,
        max_doc_candidates=3,
        max_patch_lines=200,
        doc_path_allowlist=["README.md", "docs/"],
    )
    return base.model_copy(update=overrides)


def make_snapshot(diff_text: str | None = None) -> PullRequestSnapshot:
    return PullRequestSnapshot(
        repo="acme/project",
        pr_number=7,
        title="Add timeout parameter",
        body="Updates the API call to accept timeout.",
        base_sha="base123",
        head_sha="head123",
        changed_files=[
            ChangedFile(
                path="src/client.py",
                patch=diff_text
                or """@@
-def fetch_data(url):
+def fetch_data(url, timeout=30):
     return call(url)
""",
            )
        ],
        diff_text=diff_text
        or """diff --git a/src/client.py b/src/client.py
@@
-def fetch_data(url):
+def fetch_data(url, timeout=30):
     return call(url)
""",
        doc_files={
            "README.md": """# Project

## API

Use `fetch_data(url)` to request data.
""",
            "docs/cli.md": """# CLI

## Usage

Run the command from a terminal.
""",
        },
    )


def make_payload() -> dict:
    return {
        "action": "opened",
        "repository": {"full_name": "acme/project"},
        "pull_request": {"number": 7, "head": {"sha": "head123"}},
    }


def test_retriever_selects_expected_section() -> None:
    snapshot = make_snapshot()
    settings = make_settings()
    from docsync.analysis import analyze_pull_request

    intent = analyze_pull_request(snapshot, settings.max_diff_lines)
    results = retrieve_context(snapshot.doc_files, intent, settings.max_doc_candidates)
    assert results
    assert results[0].doc_path == "README.md"
    assert results[0].section_title == "API"


def test_workflow_happy_path_publishes_patch_preview() -> None:
    snapshot = make_snapshot()
    github = FakeGitHubClient(snapshot)
    llm = StubLLMClient(
        GenerationDecision(
            decision="update",
            confidence=0.92,
            comment="Document the new timeout parameter.",
            proposed_changes=[
                {
                    "doc_path": "README.md",
                    "section_title": "API",
                    "operation": "append",
                    "content": "- `timeout` controls request timeout in seconds.",
                    "rationale": "The API signature changed.",
                }
            ],
        )
    )
    workflow = DocSyncWorkflow(make_settings(), github, llm)

    result = workflow.invoke(make_payload())

    assert result["outcome"] == "commented"
    assert llm.calls == 1
    assert github.published_bodies
    assert "timeout" in github.published_bodies[0]
    expected_seed = "acme/project#7:head123"
    assert result["session_id"] == hashlib.sha256(expected_seed.encode("utf-8")).hexdigest()[:16]


def test_oversized_diff_skips_generation_and_falls_back() -> None:
    large_diff = "\n".join(f"+line {index}" for index in range(1205))
    snapshot = make_snapshot(diff_text=large_diff)
    github = FakeGitHubClient(snapshot)
    llm = StubLLMClient(
        GenerationDecision(decision="update", confidence=0.9, comment="unused", proposed_changes=[])
    )
    workflow = DocSyncWorkflow(make_settings(max_diff_lines=1000), github, llm)

    result = workflow.invoke(make_payload())

    assert result["outcome"] == "commented"
    assert llm.calls == 0
    assert "max_diff_lines_exceeded" in github.published_bodies[0]


def test_validator_rejects_path_outside_allowlist() -> None:
    snapshot = make_snapshot()
    github = FakeGitHubClient(snapshot)
    llm = StubLLMClient(
        GenerationDecision(
            decision="update",
            confidence=0.9,
            comment="Bad path.",
            proposed_changes=[
                {
                    "doc_path": "notes/todo.md",
                    "section_title": "Todo",
                    "operation": "append",
                    "content": "- unexpected change",
                    "rationale": "invalid",
                }
            ],
        )
    )
    workflow = DocSyncWorkflow(make_settings(), github, llm)

    result = workflow.invoke(make_payload())

    assert result["validation_report"].status == "fallback_comment"
    assert "outside allowlist" in github.published_bodies[0]


def test_llm_client_retries_once_on_invalid_schema() -> None:
    responses = iter(
        [
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": '{"decision":"update"}'}}]},
            ),
            httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": """
                                {
                                  "decision": "skip",
                                  "confidence": 0.2,
                                  "comment": "Need more context",
                                  "proposed_changes": []
                                }
                                """
                            }
                        }
                    ]
                },
            ),
        ]
    )
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return next(responses)

    transport = httpx.MockTransport(handler)
    settings = make_settings(llm_provider="openai", llm_api_base_url="https://llm.test", llm_model="gpt-test")
    client = OpenAICompatibleLLMClient(settings, transport=transport)
    decision = client.generate_decision(
        payload={
            "policy": "safe",
            "pr_card": "demo",
            "diff_summary": "demo",
            "retrieved_contexts": [],
            "allowed_doc_paths": ["README.md"],
        }
    )

    assert decision.decision == "skip"
    assert calls["count"] == 2

