from __future__ import annotations

import re

from .models import ChangeIntent, PullRequestSnapshot

FUNCTION_RE = re.compile(r"^\+\s*def\s+([A-Za-z0-9_]*)\((.*?)\)\s*(?:->\s*[^:]+)?:", re.MULTILINE)
CLI_RE = re.compile(r"^\+\s*.*add_argument\(['\"](--[A-Za-z0-9_-]+)", re.MULTILINE)
CLASS_RE = re.compile(r"^\+\s*class\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def _first_lines(text: str, limit: int = 20) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:limit])


def analyze_pull_request(snapshot: PullRequestSnapshot, max_diff_lines: int) -> ChangeIntent:
    diff_lines = snapshot.diff_text.splitlines()
    path_hints = [path for path in snapshot.doc_files]
    changed_paths = [item.path for item in snapshot.changed_files]
    source_paths = [path for path in changed_paths if not path.endswith(".md")]
    path_hints.extend(source_paths)

    if len(diff_lines) > max_diff_lines:
        return ChangeIntent(
            supported=False,
            scenario="oversized_diff",
            confidence=1.0,
            summary="Diff exceeds the configured processing budget.",
            reason="max_diff_lines_exceeded",
            diff_excerpt=_first_lines(snapshot.diff_text),
            symbol_hints=[],
            path_hints=source_paths,
        )

    if not source_paths:
        return ChangeIntent(
            supported=False,
            scenario="docs_only",
            confidence=0.9,
            summary="PR changes documentation only.",
            reason="no_code_changes",
            diff_excerpt=_first_lines(snapshot.diff_text),
            symbol_hints=[],
            path_hints=[],
        )

    symbols: list[str] = []
    scenario = "unsupported"
    summary = "Code changes need manual documentation review."
    confidence = 0.4

    if cli_match := CLI_RE.search(snapshot.diff_text):
        option = cli_match.group(1)
        symbols.append(option)
        scenario = "cli_change"
        summary = f"CLI option change detected for {option}."
        confidence = 0.82
    elif func_match := FUNCTION_RE.search(snapshot.diff_text):
        function_name = func_match.group(1)
        params = [part.strip() for part in func_match.group(2).split(",") if part.strip()]
        symbols.extend([function_name, *params])
        scenario = "api_signature_change"
        summary = f"Function signature change detected for {function_name}."
        confidence = 0.8
    elif class_match := CLASS_RE.search(snapshot.diff_text):
        class_name = class_match.group(1)
        symbols.append(class_name)
        scenario = "module_addition"
        summary = f"New class or module entrypoint detected for {class_name}."
        confidence = 0.7
    elif "rename" in snapshot.title.lower():
        scenario = "rename"
        summary = "Possible rename detected from PR metadata."
        confidence = 0.65

    supported = scenario in {"cli_change", "api_signature_change", "module_addition", "rename"}
    reason = "supported_change" if supported else "unsupported_change_type"

    return ChangeIntent(
        supported=supported,
        scenario=scenario,
        confidence=confidence,
        summary=summary,
        reason=reason,
        diff_excerpt=_first_lines(snapshot.diff_text),
        symbol_hints=list(dict.fromkeys(symbols)),
        path_hints=changed_paths,
    )

