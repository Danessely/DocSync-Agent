from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ChangedFile(BaseModel):
    path: str
    status: str = "modified"
    patch: str | None = None


class PullRequestSnapshot(BaseModel):
    repo: str
    pr_number: int
    title: str
    body: str = ""
    base_sha: str = ""
    head_sha: str
    head_ref: str = ""
    changed_files: list[ChangedFile] = Field(default_factory=list)
    diff_text: str
    doc_files: dict[str, str] = Field(default_factory=dict)
    doc_file_shas: dict[str, str] = Field(default_factory=dict)


class ChangeIntent(BaseModel):
    supported: bool
    scenario: str
    confidence: float
    summary: str
    reason: str
    diff_excerpt: str
    symbol_hints: list[str] = Field(default_factory=list)
    path_hints: list[str] = Field(default_factory=list)


class RetrievedContext(BaseModel):
    doc_path: str
    section_title: str
    excerpt: str
    score: float
    selection_reason: str


class GenerationInput(BaseModel):
    policy: str
    pr_card: str
    diff_summary: str
    retrieved_contexts: list[RetrievedContext] = Field(default_factory=list)
    allowed_doc_paths: list[str] = Field(default_factory=list)


class ProposedDocChange(BaseModel):
    doc_path: str
    section_title: str
    operation: Literal["append", "replace_section"] = "append"
    content: str
    rationale: str = ""


class GenerationDecision(BaseModel):
    decision: Literal["update", "ask_human", "skip"]
    confidence: float
    comment: str
    proposed_changes: list[ProposedDocChange] = Field(default_factory=list)


class PatchEntry(BaseModel):
    doc_path: str
    old_content: str
    new_content: str
    diff_preview: str


class DocPatch(BaseModel):
    entries: list[PatchEntry] = Field(default_factory=list)
    summary: str = ""


class ValidationReport(BaseModel):
    status: Literal["valid_patch", "fallback_comment", "invalid"]
    is_valid: bool
    reasons: list[str] = Field(default_factory=list)
    allowed_doc_paths: list[str] = Field(default_factory=list)
    patch_stats: dict[str, int] = Field(default_factory=dict)


class PublishResult(BaseModel):
    mode: str
    published: bool
    comment_body: str = ""
    comment_id: int | None = None
    commit_shas: list[str] = Field(default_factory=list)
    committed_files: list[str] = Field(default_factory=list)
    details: str | None = None
    error: str | None = None


class ClarificationResult(BaseModel):
    channel: str
    sent: bool
    message: str
    error: str | None = None
