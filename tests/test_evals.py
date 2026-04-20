from __future__ import annotations

import json

from docsync.config import Settings
from docsync.evals import main, run_eval_suite


def test_run_eval_suite_passes_checked_in_cases() -> None:
    suite = run_eval_suite(
        "evals/cases",
        settings=Settings(
            llm_provider="mock",
            doc_path_allowlist=["README.md", "docs/"],
            docs_validation_command="",
            session_store_path="",
        ),
    )

    assert suite.total >= 7
    assert suite.failed == 0


def test_eval_main_returns_nonzero_for_failed_case(tmp_path, capsys, monkeypatch) -> None:
    case = {
        "name": "failing-case",
        "scenario": "failure",
        "event_payload": {
            "action": "opened",
            "repository": {"full_name": "acme/project"},
            "pull_request": {"number": 999, "head": {"sha": "head999"}},
        },
        "pr_snapshot": {
            "repo": "acme/project",
            "pr_number": 999,
            "title": "Add timeout parameter",
            "body": "",
            "base_sha": "base999",
            "head_sha": "head999",
            "head_ref": "feature/failure",
            "changed_files": [
                {
                    "path": "src/client.py",
                    "status": "modified",
                    "patch": "@@\n-def fetch_data(url):\n+def fetch_data(url, timeout=30):\n",
                }
            ],
            "diff_text": "diff --git a/src/client.py b/src/client.py\n@@\n-def fetch_data(url):\n+def fetch_data(url, timeout=30):\n",
            "doc_files": {
                "README.md": "# Project\n\n## API\n\nUse `fetch_data(url)`.\n"
            },
            "doc_file_shas": {"README.md": "sha-failure"},
        },
        "expectation": {
            "expected_outcomes": ["ignored"]
        },
    }
    case_path = tmp_path / "failing-case.json"
    case_path.write_text(json.dumps(case), encoding="utf-8")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    exit_code = main([str(case_path), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["failed"] == 1
    assert payload["results"][0]["passed"] is False
