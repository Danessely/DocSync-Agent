from __future__ import annotations

import difflib
import re

from ..models import DocPatch, GenerationDecision, PatchEntry

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def _find_section_span(content: str, section_title: str) -> tuple[int, int, int] | None:
    matches = list(HEADING_RE.finditer(content))
    for index, match in enumerate(matches):
        title = match.group(2).strip()
        if title != section_title:
            continue
        heading_end = match.end()
        section_end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        return match.start(), heading_end, section_end
    return None


class PatchBuilder:
    def build(self, doc_files: dict[str, str], decision: GenerationDecision) -> DocPatch:
        entries: list[PatchEntry] = []
        for change in decision.proposed_changes:
            old_content = doc_files.get(change.doc_path, "")
            new_content = self._apply_change(old_content, change.section_title, change.operation, change.content)
            diff_preview = "\n".join(
                difflib.unified_diff(
                    old_content.splitlines(),
                    new_content.splitlines(),
                    fromfile=change.doc_path,
                    tofile=change.doc_path,
                    lineterm="",
                )
            )
            entries.append(
                PatchEntry(
                    doc_path=change.doc_path,
                    old_content=old_content,
                    new_content=new_content,
                    diff_preview=diff_preview,
                )
            )
        summary = decision.comment or "Generated documentation patch."
        return DocPatch(entries=entries, summary=summary)

    def _apply_change(
        self,
        old_content: str,
        section_title: str,
        operation: str,
        content: str,
    ) -> str:
        cleaned = content.strip()
        span = _find_section_span(old_content, section_title)

        if span is None:
            heading = f"\n## {section_title}\n\n{cleaned}\n"
            return f"{old_content.rstrip()}{heading}\n"

        section_start, body_start, section_end = span
        section_heading = old_content[section_start:body_start]
        section_body = old_content[body_start:section_end].rstrip()

        if operation == "replace_section":
            replacement = f"{section_heading}\n{cleaned}\n"
            return f"{old_content[:section_start]}{replacement}{old_content[section_end:]}"

        appended_body = section_body
        if appended_body:
            appended_body = f"{appended_body}\n\n{cleaned}\n"
        else:
            appended_body = f"\n{cleaned}\n"
        replacement = f"{section_heading}{appended_body}"
        return f"{old_content[:section_start]}{replacement}{old_content[section_end:]}"

