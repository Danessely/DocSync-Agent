from __future__ import annotations

import hashlib
from docsync.adapters.llm import ChatOpenAILLMClient
from docsync.config import Settings
from docsync.graph.workflow import DocSyncWorkflow
from docsync.models import ChangeIntent, ChangedFile, GenerationDecision, PublishResult, PullRequestSnapshot, RetrievedContext
from docsync.retrieval.search import retrieve_context
from docsync.state_store import InMemorySessionStore


class FakeGitHubClient:
    def __init__(self, snapshot: PullRequestSnapshot) -> None:
        self.snapshot = snapshot
        self.published_bodies: list[str] = []
        self.published_patches: list[dict[str, object]] = []

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

    def publish_patch(
        self,
        snapshot: PullRequestSnapshot,
        patch,
        session_id: str,
        summary: str,
    ) -> PublishResult:
        self.published_patches.append(
            {
                "repo": snapshot.repo,
                "pr_number": snapshot.pr_number,
                "files": [entry.doc_path for entry in patch.entries],
                "session_id": session_id,
                "summary": summary,
            }
        )
        return PublishResult(
            mode="commit_patch",
            published=True,
            commit_shas=["commit123"],
            committed_files=[entry.doc_path for entry in patch.entries],
            details=summary,
        )


class FakeTelegramClient:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, text: str):
        self.messages.append(text)
        return {"channel": "telegram", "sent": True, "message": text}


class StubLLMClient:
    def __init__(
        self,
        decision: GenerationDecision,
        analysis: ChangeIntent | None = None,
        selected_contexts: list[RetrievedContext] | None = None,
    ) -> None:
        self.decision = decision
        self.analysis = analysis
        self.selected_contexts = selected_contexts or []
        self.calls = 0

    def analyze_change(self, snapshot):
        del snapshot
        if self.analysis is None:
            raise RuntimeError("analysis_unavailable")
        return self.analysis

    def select_retrieved_contexts(self, intent, candidates, max_candidates):
        del intent, candidates
        return self.selected_contexts[:max_candidates]

    def generate_decision(self, payload):
        self.calls += 1
        return self.decision


class ClarificationAwareLLMClient:
    def __init__(self) -> None:
        self.calls = 0

    def analyze_change(self, snapshot):
        return ChangeIntent(
            supported=True,
            scenario="behavior_change",
            confidence=0.9,
            summary="Behavior change detected that needs documentation.",
            reason="llm_analysis",
            diff_excerpt="",
            symbol_hints=["timeout"],
            path_hints=[item.path for item in snapshot.changed_files],
            documentation_hints=["behavior", "timeout"],
        )

    def select_retrieved_contexts(self, intent, candidates, max_candidates):
        del intent
        return candidates[:max_candidates]

    def generate_decision(self, payload):
        self.calls += 1
        if not getattr(payload, "human_clarification", ""):
            return GenerationDecision(
                decision="ask_human",
                confidence=0.2,
                comment="Need clarification about the intended docs update.",
                proposed_changes=[],
            )
        return GenerationDecision(
            decision="update",
            confidence=0.95,
            comment="Use the human clarification to update the docs.",
            proposed_changes=[
                {
                    "doc_path": "README.md",
                    "section_title": "API",
                    "operation": "append",
                    "content": f"- Human clarification: {payload.human_clarification}",
                    "rationale": "User clarified expected behavior.",
                }
            ],
        )


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
        head_ref="feature/docsync",
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
        doc_file_shas={"README.md": "sha-readme", "docs/cli.md": "sha-cli"},
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


def test_retriever_uses_llm_reranking() -> None:
    intent = ChangeIntent(
        supported=True,
        scenario="behavior_change",
        confidence=0.88,
        summary="Retry behavior changed for failed sync operations.",
        reason="llm_analysis",
        diff_excerpt="",
        symbol_hints=["retry"],
        path_hints=["src/sync.py"],
        documentation_hints=["failure handling", "retries"],
    )
    doc_files = {
        "README.md": "# Project\n\n## Overview\nGeneral introduction.\n",
        "docs/operations.md": (
            "# Operations\n\n## Failure handling\n"
            "Failed sync runs are retried with exponential backoff and a capped delay.\n"
        ),
    }
    llm = StubLLMClient(
        decision=GenerationDecision(decision="skip", confidence=0.1, comment="unused", proposed_changes=[]),
        selected_contexts=[
            RetrievedContext(
                doc_path="docs/operations.md",
                section_title="Failure handling",
                excerpt="Failed sync runs are retried with exponential backoff and a capped delay.\n",
                score=0.97,
                selection_reason="Best semantic match for retry behavior documentation.",
            )
        ],
    )

    results = retrieve_context(doc_files, intent, max_candidates=2, llm_client=llm)

    assert results
    assert results[0].doc_path == "docs/operations.md"
    assert "semantic match" in results[0].selection_reason


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


def test_llm_analysis_supports_non_obvious_change() -> None:
    snapshot = PullRequestSnapshot(
        repo="acme/project",
        pr_number=11,
        title="Adjust retry scheduler behavior",
        body="Avoid aggressive retries after transient failures.",
        base_sha="base456",
        head_sha="head456",
        head_ref="feature/retry-docs",
        changed_files=[
            ChangedFile(
                path="src/retry.py",
                patch="""@@
- next_delay = min(current_delay * 2, 30)
+ next_delay = min(current_delay * 3, 90)
""",
            )
        ],
        diff_text="""diff --git a/src/retry.py b/src/retry.py
@@
- next_delay = min(current_delay * 2, 30)
+ next_delay = min(current_delay * 3, 90)
""",
        doc_files={
            "docs/operations.md": """# Operations

## Failure handling

Retries back off after transient failures.
""",
        },
        doc_file_shas={"docs/operations.md": "sha-ops"},
    )
    github = FakeGitHubClient(snapshot)
    llm = StubLLMClient(
        analysis=ChangeIntent(
            supported=True,
            scenario="behavior_change",
            confidence=0.91,
            summary="Retry backoff behavior changed after transient failures.",
            reason="llm_analysis",
            diff_excerpt="",
            symbol_hints=["next_delay"],
            path_hints=["src/retry.py"],
            documentation_hints=["retry backoff", "failure handling"],
        ),
        decision=GenerationDecision(
            decision="update",
            confidence=0.92,
            comment="Document the new retry backoff behavior.",
            proposed_changes=[
                {
                    "doc_path": "docs/operations.md",
                    "section_title": "Failure handling",
                    "operation": "append",
                    "content": "- Retry backoff now grows more aggressively and caps at 90 seconds.",
                    "rationale": "The user-visible retry behavior changed.",
                }
            ],
        ),
    )
    workflow = DocSyncWorkflow(make_settings(), github, llm)

    result = workflow.run_once(
        {
            "action": "opened",
            "repository": {"full_name": "acme/project"},
            "pull_request": {"number": 11, "head": {"sha": "head456"}},
        }
    )

    assert result["change_intent"].scenario == "behavior_change"
    assert result["change_intent"].reason == "llm_analysis"
    assert result["outcome"] == "commented"
    assert github.published_bodies
    assert "retry backoff" in github.published_bodies[0].lower()


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


def test_workflow_commit_patch_mode_publishes_patch_to_branch() -> None:
    snapshot = make_snapshot()
    github = FakeGitHubClient(snapshot)
    llm = StubLLMClient(
        GenerationDecision(
            decision="update",
            confidence=0.95,
            comment="Document the timeout parameter.",
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
    workflow = DocSyncWorkflow(make_settings(publish_mode="commit_patch"), github, llm)

    result = workflow.run_once(make_payload())

    assert result["outcome"] == "patched"
    assert github.published_patches
    assert github.published_patches[0]["files"] == ["README.md"]
    assert not github.published_bodies


def test_low_confidence_routes_to_telegram_clarification() -> None:
    snapshot = make_snapshot()
    github = FakeGitHubClient(snapshot)
    telegram = FakeTelegramClient()
    llm = StubLLMClient(
        GenerationDecision(
            decision="update",
            confidence=0.3,
            comment="I am not confident which docs should change.",
            proposed_changes=[
                {
                    "doc_path": "README.md",
                    "section_title": "API",
                    "operation": "append",
                    "content": "- possible update",
                    "rationale": "uncertain",
                }
            ],
        )
    )
    workflow = DocSyncWorkflow(
        make_settings(min_confidence=0.6),
        github,
        llm,
        telegram_client=telegram,
        state_store=InMemorySessionStore(),
    )

    result = workflow.run_once(make_payload())

    assert result["outcome"] == "asked_human"
    assert telegram.messages
    assert "confidence is too low" in telegram.messages[0]
    assert not github.published_bodies
    assert not github.published_patches


def test_validation_failure_routes_to_telegram_clarification() -> None:
    snapshot = make_snapshot()
    github = FakeGitHubClient(snapshot)
    telegram = FakeTelegramClient()
    llm = StubLLMClient(
        GenerationDecision(
            decision="update",
            confidence=0.95,
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
    workflow = DocSyncWorkflow(
        make_settings(),
        github,
        llm,
        telegram_client=telegram,
        state_store=InMemorySessionStore(),
    )

    result = workflow.run_once(make_payload())

    assert result["outcome"] == "asked_human"
    assert telegram.messages
    assert "outside allowlist" in telegram.messages[0]
    assert not github.published_bodies


def test_resume_from_clarification_publishes_after_user_reply() -> None:
    snapshot = make_snapshot()
    github = FakeGitHubClient(snapshot)
    telegram = FakeTelegramClient()
    store = InMemorySessionStore()
    llm = ClarificationAwareLLMClient()
    workflow = DocSyncWorkflow(
        make_settings(),
        github,
        llm,
        telegram_client=telegram,
        state_store=store,
    )

    initial = workflow.run_once(make_payload())
    session_id = initial["session_id"]
    assert initial["outcome"] == "asked_human"
    assert telegram.messages

    resumed = workflow.resume_from_clarification(session_id, "The timeout parameter should be documented in README.")

    assert resumed["outcome"] == "commented"
    assert llm.calls == 2
    assert github.published_bodies
    assert "Human clarification" in github.published_bodies[0]
    assert store.get_pending_clarification(session_id) is None


def test_llm_client_retries_once_on_invalid_schema() -> None:
    calls = {"count": 0}
    structured_calls: list[tuple[str, str, bool]] = []

    class FakeStructuredRunnable:
        def __init__(self) -> None:
            self._responses = iter(
                [
                    ValueError("invalid schema"),
                    GenerationDecision(
                        decision="skip",
                        confidence=0.2,
                        comment="Need more context",
                        proposed_changes=[],
                    ),
                ]
            )

        def invoke(self, messages):
            del messages
            calls["count"] += 1
            result = next(self._responses)
            if isinstance(result, Exception):
                raise result
            return result

    class FakeChatModel:
        def with_structured_output(self, response_model, method, strict):
            structured_calls.append((response_model.__name__, method, strict))
            return FakeStructuredRunnable()

    settings = make_settings(llm_provider="openai", llm_api_base_url="https://llm.test", llm_model="gpt-test")
    client = ChatOpenAILLMClient(settings, chat_model=FakeChatModel())
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
    assert structured_calls == [("GenerationDecision", "json_schema", True)]
