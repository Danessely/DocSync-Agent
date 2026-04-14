from __future__ import annotations

import os
import re

from ..models import ChangeIntent, RetrievedContext
from .index import split_markdown_sections

TOKEN_RE = re.compile(r"[A-Za-z0-9_-]+")


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text)}


def retrieve_context(
    doc_files: dict[str, str],
    intent: ChangeIntent,
    max_candidates: int,
) -> list[RetrievedContext]:
    if not doc_files:
        return []

    query_tokens = _tokenize(intent.summary)
    query_tokens.update(token.lower() for token in intent.symbol_hints)
    query_tokens.update(os.path.splitext(os.path.basename(path))[0].lower() for path in intent.path_hints)

    scored: list[RetrievedContext] = []
    for doc_path, content in doc_files.items():
        path_bonus = 0.35 if doc_path == "README.md" else 0.1
        for section in split_markdown_sections(doc_path, content):
            haystack = f"{doc_path}\n{section.section_title}\n{section.content}"
            section_tokens = _tokenize(haystack)
            overlap = query_tokens.intersection(section_tokens)
            if not overlap:
                continue
            lexical = len(overlap) / max(len(query_tokens), 1)
            density = min(len(overlap) / 5.0, 1.0)
            score = round(lexical + density + path_bonus, 3)
            scored.append(
                RetrievedContext(
                    doc_path=doc_path,
                    section_title=section.section_title,
                    excerpt=section.content[:1200],
                    score=score,
                    selection_reason=f"token overlap: {', '.join(sorted(overlap)[:5])}",
                )
            )

    scored.sort(key=lambda item: item.score, reverse=True)
    deduped: list[RetrievedContext] = []
    seen: set[tuple[str, str]] = set()
    for item in scored:
        key = (item.doc_path, item.section_title)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= max_candidates:
            break
    return deduped

