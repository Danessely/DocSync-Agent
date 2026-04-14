from __future__ import annotations

import difflib

from ..config import Settings
from ..models import DocPatch, PullRequestSnapshot, ValidationReport


def _is_allowed(path: str, allowlist: list[str]) -> bool:
    for allowed in allowlist:
        if allowed.endswith("/") and path.startswith(allowed):
            return True
        if path == allowed:
            return True
    return False


class PatchValidator:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

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

        if stats["patch_lines"] > self._settings.max_patch_lines:
            reasons.append("Patch exceeds the configured size limit.")

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

