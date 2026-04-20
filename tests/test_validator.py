from __future__ import annotations

import subprocess
from pathlib import Path

from docsync.config import Settings
from docsync.models import DocPatch, PatchEntry, PullRequestSnapshot
from docsync.validation.validator import PatchValidator


def make_snapshot() -> PullRequestSnapshot:
    return PullRequestSnapshot(
        repo="acme/project",
        pr_number=12,
        title="Update docs",
        body="",
        base_sha="base123",
        head_sha="head123",
        head_ref="feature/docs",
        diff_text="diff --git a/src/client.py b/src/client.py",
        changed_files=[],
        doc_files={
            "README.md": "# Project\n\n## API\n\nUse `fetch_data(url)`.\n",
            "docs/guide.md": "# Guide\n\n## Usage\n\nStart here.\n",
            "mkdocs.yml": "site_name: DocSync\nnav:\n  - Home: README.md\n",
        },
    )


def make_patch(new_content: str) -> DocPatch:
    return DocPatch(
        entries=[
            PatchEntry(
                doc_path="README.md",
                old_content="# Project\n\n## API\n\nUse `fetch_data(url)`.\n",
                new_content=new_content,
                diff_preview="",
            )
        ],
        summary="Update docs.",
    )


def test_validator_rejects_unbalanced_markdown_fence() -> None:
    validator = PatchValidator(Settings(doc_path_allowlist=["README.md", "docs/", "mkdocs.yml"]))
    snapshot = make_snapshot()
    patch = make_patch("# Project\n\n## API\n\n```python\nprint('oops')\n")

    report = validator.validate(snapshot, patch)

    assert report.is_valid is False
    assert "unbalanced fenced code block" in report.reasons[0]


def test_validator_runs_docs_validation_command_against_temp_workspace() -> None:
    seen: dict[str, str] = {}

    def fake_runner(args, *, cwd, timeout, capture_output, text):
        del timeout, capture_output, text
        seen["cwd"] = cwd
        seen["args"] = " ".join(args)
        assert (Path(cwd) / "README.md").exists()
        assert (Path(cwd) / "docs" / "guide.md").exists()
        assert (Path(cwd) / "mkdocs.yml").exists()
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    validator = PatchValidator(
        Settings(
            doc_path_allowlist=["README.md", "docs/", "mkdocs.yml"],
            docs_validation_command="mkdocs build --strict",
        ),
        command_runner=fake_runner,
    )
    snapshot = make_snapshot()
    patch = make_patch("# Project\n\n## API\n\nUse `fetch_data(url, timeout=30)`.\n")

    report = validator.validate(snapshot, patch)

    assert report.is_valid is True
    assert seen["args"] == "mkdocs build --strict"


def test_validator_surfaces_docs_validation_command_failure() -> None:
    def fake_runner(args, *, cwd, timeout, capture_output, text):
        del args, cwd, timeout, capture_output, text
        return subprocess.CompletedProcess(args=["mkdocs"], returncode=1, stdout="", stderr="mkdocs failed")

    validator = PatchValidator(
        Settings(
            doc_path_allowlist=["README.md", "docs/", "mkdocs.yml"],
            docs_validation_command="mkdocs build --strict",
        ),
        command_runner=fake_runner,
    )
    snapshot = make_snapshot()
    patch = make_patch("# Project\n\n## API\n\nUse `fetch_data(url, timeout=30)`.\n")

    report = validator.validate(snapshot, patch)

    assert report.is_valid is False
    assert report.reasons == ["Docs validation command failed: mkdocs failed"]
