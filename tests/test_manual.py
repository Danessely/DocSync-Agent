from __future__ import annotations

import json

from docsync.manual import SnapshotBundle, load_snapshot_bundle, main, run_snapshot
from docsync.models import ChangedFile, GenerationDecision, PullRequestSnapshot
from docsync.config import Settings


class StubLLMClient:
    def __init__(self, decision: GenerationDecision) -> None:
        self.decision = decision

    def generate_decision(self, payload):
        return self.decision


def make_bundle() -> SnapshotBundle:
    snapshot = PullRequestSnapshot(
        repo="acme/project",
        pr_number=10,
        title="Add timeout parameter",
        body="Snapshot replay for local testing.",
        base_sha="base123",
        head_sha="head123",
        head_ref="feature/docsync",
        changed_files=[
            ChangedFile(
                path="src/client.py",
                patch="""@@
-def fetch_data(url):
+def fetch_data(url, timeout=30):
     return call(url)
""",
            )
        ],
        diff_text="""diff --git a/src/client.py b/src/client.py
@@
-def fetch_data(url):
+def fetch_data(url, timeout=30):
     return call(url)
""",
        doc_files={
            "README.md": """# Project

## API

Use `fetch_data(url)` to request data.
"""
        },
        doc_file_shas={"README.md": "sha-readme"},
    )
    return SnapshotBundle(
        event_payload={
            "action": "opened",
            "repository": {"full_name": "acme/project"},
            "pull_request": {"number": 10, "head": {"sha": "head123"}},
        },
        pr_snapshot=snapshot,
    )


def test_load_snapshot_bundle(tmp_path) -> None:
    bundle = make_bundle()
    path = tmp_path / "snapshot.json"
    path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

    loaded = load_snapshot_bundle(path)

    assert loaded.pr_snapshot.repo == "acme/project"
    assert loaded.event_payload["pull_request"]["number"] == 10


def test_run_snapshot_returns_comment_preview(tmp_path) -> None:
    bundle = make_bundle()
    path = tmp_path / "snapshot.json"
    path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")

    result, comments = run_snapshot(
        path,
        settings=Settings(doc_path_allowlist=["README.md", "docs/"]),
        llm_client=StubLLMClient(
            GenerationDecision(
                decision="update",
                confidence=0.9,
                comment="Document the timeout parameter.",
                proposed_changes=[
                    {
                        "doc_path": "README.md",
                        "section_title": "API",
                        "operation": "append",
                        "content": "- `timeout` controls request timeout in seconds.",
                        "rationale": "The API changed.",
                    }
                ],
            )
        ),
    )

    assert result["outcome"] == "commented"
    assert comments
    assert "timeout" in comments[0]


def test_manual_main_json_output(tmp_path, capsys, monkeypatch) -> None:
    bundle = make_bundle()
    path = tmp_path / "snapshot.json"
    path.write_text(bundle.model_dump_json(indent=2), encoding="utf-8")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("PUBLISH_MODE", "comment_only")

    exit_code = main([str(path), "--json"])
    output = capsys.readouterr().out

    assert exit_code == 0
    payload = json.loads(output)
    assert payload["status"] == "commented"
    assert payload["comment_count"] == 1
