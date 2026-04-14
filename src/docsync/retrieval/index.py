from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class DocSection:
    doc_path: str
    section_title: str
    content: str


HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def split_markdown_sections(doc_path: str, content: str) -> list[DocSection]:
    matches = list(HEADING_RE.finditer(content))
    if not matches:
        title = "Introduction" if doc_path == "README.md" else doc_path
        return [DocSection(doc_path=doc_path, section_title=title, content=content.strip())]

    sections: list[DocSection] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        title = match.group(2).strip()
        sections.append(
            DocSection(
                doc_path=doc_path,
                section_title=title,
                content=content[start:end].strip(),
            )
        )
    return sections

