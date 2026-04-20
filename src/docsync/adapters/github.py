from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from ..models import ChangedFile, DocPatch, PublishResult, PullRequestSnapshot


class GitHubError(RuntimeError):
    """Raised for GitHub API failures."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        transient: bool = False,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.status_code = status_code
        self.transient = transient


class GitHubClient(Protocol):
    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool: ...
    def parse_pull_request_event(self, payload: dict[str, Any]) -> dict[str, Any] | None: ...
    def is_markdown_only_update(self, repo: str, before_sha: str, head_sha: str) -> bool: ...
    def load_pull_request(self, repo: str, pr_number: int) -> PullRequestSnapshot: ...
    def publish_comment(self, repo: str, pr_number: int, body: str) -> PublishResult: ...
    def publish_patch(
        self,
        snapshot: PullRequestSnapshot,
        patch: DocPatch,
        session_id: str,
        summary: str,
    ) -> PublishResult: ...


class GitHubApiClient:
    def __init__(
        self,
        token: str,
        webhook_secret: str,
        doc_allowlist: list[str],
        base_url: str = "https://api.github.com",
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        max_retries: int = 2,
        backoff_base_sec: float = 0.5,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._token = token
        self._webhook_secret = webhook_secret
        self._doc_allowlist = doc_allowlist
        self._max_retries = max(0, max_retries)
        self._backoff_base_sec = max(0.0, backoff_base_sec)
        self._sleep = sleep_fn or time.sleep
        self._default_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "docsync-agent",
        }
        if token:
            self._default_headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            headers=self._default_headers,
            transport=transport,
        )

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:
        if not self._webhook_secret:
            return True
        if not signature or not signature.startswith("sha256="):
            return False
        digest = hmac.new(
            self._webhook_secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(f"sha256={digest}", signature)

    def parse_pull_request_event(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        action = payload.get("action")
        if action not in {"opened", "reopened", "synchronize"}:
            return None
        pr = payload.get("pull_request") or {}
        repo = (payload.get("repository") or {}).get("full_name")
        if not repo or not pr.get("number"):
            return None
        return {
            "repo": repo,
            "pr_number": int(pr["number"]),
            "head_sha": ((pr.get("head") or {}).get("sha") or ""),
            "before_sha": payload.get("before") or "",
            "action": action,
        }

    def is_markdown_only_update(self, repo: str, before_sha: str, head_sha: str) -> bool:
        if not before_sha or not head_sha or before_sha == head_sha:
            return False

        response = self._request(
            "GET",
            f"/repos/{repo}/compare/{before_sha}...{head_sha}",
        )
        files = response.json().get("files") or []
        if not files:
            return False
        return all(_is_markdown_path(item.get("filename", "")) for item in files)

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        request_headers = dict(self._default_headers)
        request_headers.update(kwargs.pop("headers", {}))
        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.request(method, path, headers=request_headers, **kwargs)
            except httpx.RequestError as exc:
                error = GitHubError("transient_http_error", str(exc), transient=True)
                if attempt < self._max_retries:
                    self._sleep(self._retry_delay(attempt))
                    continue
                raise error from exc

            if response.status_code < 400:
                return response

            error = _classify_response_error(response)
            if error.transient and attempt < self._max_retries:
                self._sleep(self._retry_delay(attempt, response))
                continue
            raise error

        raise GitHubError("transient_http_error", "unreachable_retry_state", transient=True)

    def _retry_delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return max(0.0, float(retry_after))
                except ValueError:
                    pass
        return self._backoff_base_sec * (2**attempt)

    def load_pull_request(self, repo: str, pr_number: int) -> PullRequestSnapshot:
        pr_resp = self._request("GET", f"/repos/{repo}/pulls/{pr_number}")
        pr_data = pr_resp.json()

        diff_resp = self._request(
            "GET",
            f"/repos/{repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.v3.diff"},
        )

        files: list[ChangedFile] = []
        page = 1
        while True:
            files_resp = self._request(
                "GET",
                f"/repos/{repo}/pulls/{pr_number}/files",
                params={"per_page": 100, "page": page},
            )
            page_items = files_resp.json()
            if not page_items:
                break
            files.extend(
                ChangedFile(
                    path=item["filename"],
                    status=item.get("status", "modified"),
                    patch=item.get("patch"),
                )
                for item in page_items
            )
            if len(page_items) < 100:
                break
            page += 1

        head_sha = ((pr_data.get("head") or {}).get("sha") or "")
        head_ref = ((pr_data.get("head") or {}).get("ref") or "")
        base_sha = ((pr_data.get("base") or {}).get("sha") or "")
        doc_files, doc_file_shas = self._load_doc_files(repo, head_sha)

        return PullRequestSnapshot(
            repo=repo,
            pr_number=pr_number,
            title=pr_data.get("title", ""),
            body=pr_data.get("body") or "",
            base_sha=base_sha,
            head_sha=head_sha,
            head_ref=head_ref,
            changed_files=files,
            diff_text=diff_resp.text,
            doc_files=doc_files,
            doc_file_shas=doc_file_shas,
        )

    def _load_doc_files(self, repo: str, sha: str) -> tuple[dict[str, str], dict[str, str]]:
        tree_resp = self._request("GET", f"/repos/{repo}/git/trees/{sha}", params={"recursive": 1})
        tree = tree_resp.json().get("tree", [])
        doc_paths = [
            item["path"]
            for item in tree
            if item.get("type") == "blob" and self._is_allowed_path(item["path"])
        ]

        docs: dict[str, str] = {}
        doc_shas: dict[str, str] = {}
        for path in doc_paths:
            content_resp = self._request(
                "GET",
                f"/repos/{repo}/contents/{quote(path, safe='')}",
                params={"ref": sha},
            )
            content_data = content_resp.json()
            if content_data.get("encoding") != "base64":
                continue
            docs[path] = base64.b64decode(content_data["content"]).decode("utf-8")
            if content_data.get("sha"):
                doc_shas[path] = content_data["sha"]
        return docs, doc_shas

    def _is_allowed_path(self, path: str) -> bool:
        for allowed in self._doc_allowlist:
            if allowed.endswith("/") and path.startswith(allowed):
                return True
            if path == allowed:
                return True
        return False

    def publish_comment(self, repo: str, pr_number: int, body: str) -> PublishResult:
        response = self._request(
            "POST",
            f"/repos/{repo}/issues/{pr_number}/comments",
            json={"body": body},
        )
        payload = response.json()
        return PublishResult(
            mode="comment_only",
            published=True,
            comment_body=body,
            comment_id=payload.get("id"),
        )

    def publish_patch(
        self,
        snapshot: PullRequestSnapshot,
        patch: DocPatch,
        session_id: str,
        summary: str,
    ) -> PublishResult:
        if not snapshot.head_ref:
            raise GitHubError("missing_head_ref_for_patch_publish")

        commit_shas: list[str] = []
        committed_files: list[str] = []
        for entry in patch.entries:
            body: dict[str, Any] = {
                "message": f"docsync: update docs for PR #{snapshot.pr_number} ({session_id})",
                "content": base64.b64encode(entry.new_content.encode("utf-8")).decode("utf-8"),
                "branch": snapshot.head_ref,
            }
            current_sha = snapshot.doc_file_shas.get(entry.doc_path)
            if current_sha:
                body["sha"] = current_sha
            response = self._request(
                "PUT",
                f"/repos/{snapshot.repo}/contents/{quote(entry.doc_path, safe='')}",
                json=body,
            )
            payload = response.json()
            commit_sha = (payload.get("commit") or {}).get("sha")
            if commit_sha:
                commit_shas.append(commit_sha)
            committed_files.append(entry.doc_path)

        return PublishResult(
            mode="commit_patch",
            published=bool(committed_files),
            comment_body="",
            commit_shas=commit_shas,
            committed_files=committed_files,
            details=summary,
        )


def _is_markdown_path(path: str) -> bool:
    return path.lower().endswith(".md")


def _classify_response_error(response: httpx.Response) -> GitHubError:
    status = response.status_code
    body = response.text
    if status in {401, 403}:
        return GitHubError("auth_error", body, status_code=status)
    if status == 404:
        return GitHubError("not_found", body, status_code=status)
    if status == 409:
        return GitHubError("conflict", body, status_code=status)
    if status == 429:
        return GitHubError("rate_limited", body, status_code=status, transient=True)
    if 500 <= status < 600:
        return GitHubError("transient_http_error", body, status_code=status, transient=True)
    return GitHubError(f"github_http_{status}", body, status_code=status)
