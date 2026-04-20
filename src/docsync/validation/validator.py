from __future__ import annotations

import difflib
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from ..config import Settings
from ..models import DocPatch, PullRequestSnapshot, ValidationReport


class CommandRunner(Protocol):
    def __call__(
        self,
        args: list[str],
        *,
        cwd: str,
        timeout: int,
        capture_output: bool,
        text: bool,
    ) -> subprocess.CompletedProcess[str]: ...


def _is_allowed(path: str, allowlist: list[str]) -> bool:
    for allowed in allowlist:
        if allowed.endswith("/") and path.startswith(allowed):
            return True
        if path == allowed:
            return True
    return False


def _normalize_markdown_path(path: str) -> Path:
    normalized = Path(path)
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"Unsafe path for validation workspace: {path}")
    return normalized


class PatchValidator:
    def __init__(
        self,
        settings: Settings,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self._settings = settings
        self._command_runner = command_runner or subprocess.run

    def validate(self, snapshot: PullRequestSnapshot, patch: DocPatch) -> ValidationReport:
        reasons: list[str] = []
        stats = {"changed_files": 0, "patch_lines": 0}

        if not patch.entries:
            return ValidationReport(
                status="fallback_comment",
                is_valid=False,
                reasons=["No patch entries were generated."],
                allowed_doc_paths=self._settings.doc_path_allowlist,
                patch_stats=stats,
            )

        if len(patch.entries) > self._settings.max_changed_doc_files:
            reasons.append("Too many documentation files were modified.")

        patched_docs = dict(snapshot.doc_files)
        for entry in patch.entries:
            if not _is_allowed(entry.doc_path, self._settings.doc_path_allowlist):
                reasons.append(f"Path outside allowlist: {entry.doc_path}")
            if not entry.doc_path.endswith(".md"):
                reasons.append(f"Only Markdown files are supported: {entry.doc_path}")

            diff_lines = list(
                difflib.unified_diff(
                    entry.old_content.splitlines(),
                    entry.new_content.splitlines(),
                    lineterm="",
                )
            )
            stats["patch_lines"] += len(diff_lines)
            stats["changed_files"] += 1
            removed = sum(1 for line in diff_lines if line.startswith("-") and not line.startswith("---"))
            added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
            if removed > added + 20:
                reasons.append(f"Patch removes too much content from {entry.doc_path}")

            reasons.extend(_validate_markdown_content(entry.doc_path, entry.new_content))
            patched_docs[entry.doc_path] = entry.new_content

        if stats["patch_lines"] > self._settings.max_patch_lines:
            reasons.append("Patch exceeds the configured size limit.")

        if not reasons and self._settings.docs_validation_command:
            reasons.extend(self._run_docs_validation(patched_docs))

        if reasons:
            return ValidationReport(
                status="fallback_comment",
                is_valid=False,
                reasons=reasons,
                allowed_doc_paths=self._settings.doc_path_allowlist,
                patch_stats=stats,
            )

        return ValidationReport(
            status="valid_patch",
            is_valid=True,
            reasons=[],
            allowed_doc_paths=self._settings.doc_path_allowlist,
            patch_stats=stats,
        )

    def _run_docs_validation(self, doc_files: dict[str, str]) -> list[str]:
        args = shlex.split(self._settings.docs_validation_command)
        if not args:
            return []

        with tempfile.TemporaryDirectory(prefix="docsync-validate-") as tmp_dir:
            root = Path(tmp_dir)
            for path, content in doc_files.items():
                try:
                    target = root / _normalize_markdown_path(path)
                except ValueError as exc:
                    return [str(exc)]
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            try:
                result = self._command_runner(
                    args,
                    cwd=str(root),
                    timeout=self._settings.docs_validation_timeout_sec,
                    capture_output=True,
                    text=True,
                )
            except subprocess.TimeoutExpired:
                return ["Docs validation command timed out."]
            except OSError as exc:
                return [f"Docs validation command could not run: {exc}"]

            if result.returncode == 0:
                return []

            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            detail = stderr or stdout or f"exit code {result.returncode}"
            return [f"Docs validation command failed: {detail}"]


def _validate_markdown_content(path: str, content: str) -> list[str]:
    reasons: list[str] = []
    lines = content.splitlines()

    if "\x00" in content:
        reasons.append(f"Markdown contains null bytes: {path}")

    if not _has_balanced_fences(lines):
        reasons.append(f"Markdown contains an unbalanced fenced code block: {path}")

    return reasons


def _has_balanced_fences(lines: list[str]) -> bool:
    active_fence: str | None = None
    for raw_line in lines:
        stripped = raw_line.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            marker = stripped[:3]
            if active_fence is None:
                active_fence = marker
            elif active_fence == marker:
                active_fence = None
    return active_fence is None
