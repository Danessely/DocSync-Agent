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
    llm_client=None,
) -> list[RetrievedContext]:
    lexical_candidates = _collect_lexical_candidates(doc_files, intent, max_candidates)
    if not lexical_candidates:
        return []

    reranked = _rerank_with_llm(intent, lexical_candidates, max_candidates, llm_client)
    if reranked:
        return reranked
    return lexical_candidates[:max_candidates]


def _collect_lexical_candidates(
    doc_files: dict[str, str],
    intent: ChangeIntent,
    max_candidates: int,
) -> list[RetrievedContext]:
    if not doc_files:
        return []

    query_tokens = _build_query_tokens(intent)
    scored: list[RetrievedContext] = []
    candidate_budget = max(max_candidates * 4, 8)
    for doc_path, content in doc_files.items():
        path_bonus = _path_bonus(doc_path, intent.path_hints)
        for section in split_markdown_sections(doc_path, content):
            haystack = f"{doc_path}\n{section.section_title}\n{section.content}"
            section_tokens = _tokenize(haystack)
            overlap = query_tokens.intersection(section_tokens)
            lexical = len(overlap) / max(len(query_tokens), 1)
            density = min(len(overlap) / 5.0, 1.0)
            fallback_bonus = 0.12 if _is_semantic_fallback_candidate(doc_path, section.section_title, intent) else 0.0
            score = round(lexical + density + path_bonus + fallback_bonus, 3)
            if not overlap and fallback_bonus == 0.0:
                continue
            scored.append(
                RetrievedContext(
                    doc_path=doc_path,
                    section_title=section.section_title,
                    excerpt=section.content[:1200],
                    score=score,
                    selection_reason=_selection_reason(overlap, path_bonus, fallback_bonus),
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
        if len(deduped) >= candidate_budget:
            break
    return deduped


def _build_query_tokens(intent: ChangeIntent) -> set[str]:
    query_tokens = _tokenize(intent.summary)
    query_tokens.update(token.lower() for token in intent.symbol_hints)
    query_tokens.update(token.lower() for token in intent.documentation_hints)
    query_tokens.update(os.path.splitext(os.path.basename(path))[0].lower() for path in intent.path_hints)
    for path in intent.path_hints:
        query_tokens.update(part.lower() for part in path.split("/") if part)
    return query_tokens


def _path_bonus(doc_path: str, path_hints: list[str]) -> float:
    if doc_path == "README.md":
        return 0.35
    for path in path_hints:
        stem = os.path.splitext(os.path.basename(path))[0].lower()
        if stem and stem in doc_path.lower():
            return 0.25
    return 0.1


def _is_semantic_fallback_candidate(doc_path: str, section_title: str, intent: ChangeIntent) -> bool:
    lowered_title = section_title.lower()
    if doc_path == "README.md":
        return True
    semantic_terms = [term.lower() for term in intent.documentation_hints]
    return any(term in lowered_title or term in doc_path.lower() for term in semantic_terms)


def _selection_reason(overlap: set[str], path_bonus: float, fallback_bonus: float) -> str:
    reasons: list[str] = []
    if overlap:
        reasons.append(f"token overlap: {', '.join(sorted(overlap)[:5])}")
    if path_bonus >= 0.25:
        reasons.append("strong path affinity")
    elif path_bonus > 0.1:
        reasons.append("path affinity")
    if fallback_bonus:
        reasons.append("semantic fallback candidate")
    return "; ".join(reasons) if reasons else "broad lexical candidate"


def _rerank_with_llm(
    intent: ChangeIntent,
    candidates: list[RetrievedContext],
    max_candidates: int,
    llm_client,
) -> list[RetrievedContext]:
    selector = getattr(llm_client, "select_retrieved_contexts", None)
    if selector is None:
        return []

    try:
        selected = selector(intent, candidates, max_candidates)
    except Exception:
        return []

    candidate_lookup = {(item.doc_path, item.section_title): item for item in candidates}
    validated: list[RetrievedContext] = []
    seen: set[tuple[str, str]] = set()
    for item in selected:
        if not isinstance(item, RetrievedContext):
            continue
        key = (item.doc_path, item.section_title)
        candidate = candidate_lookup.get(key)
        if candidate is None:
            continue
        if key in seen:
            continue
        seen.add(key)
        validated.append(
            candidate.model_copy(
                update={
                    "score": item.score,
                    "selection_reason": item.selection_reason,
                }
            )
        )
        if len(validated) >= max_candidates:
            break
    return validated
