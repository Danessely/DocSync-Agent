from __future__ import annotations

import base64

import httpx

from docsync.adapters.github import GitHubApiClient


def test_load_pull_request_preserves_authorization_on_all_requests() -> None:
    seen_headers: list[tuple[str, str | None, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(
            (
                str(request.url),
                request.headers.get("authorization"),
                request.headers.get("accept"),
            )
        )
        url = str(request.url)
        if url.endswith("/pulls/7") and request.headers.get("accept") == "application/vnd.github+json":
            return httpx.Response(
                200,
                json={"title": "Add timeout", "body": "", "head": {"sha": "head123"}, "base": {"sha": "base123"}},
            )
        if url.endswith("/pulls/7") and request.headers.get("accept") == "application/vnd.github.v3.diff":
            return httpx.Response(200, text="diff --git a/src/client.py b/src/client.py")
        if "/pulls/7/files" in url:
            return httpx.Response(200, json=[{"filename": "src/client.py", "status": "modified", "patch": "@@"}])
        if "/git/trees/head123" in url:
            return httpx.Response(200, json={"tree": [{"path": "README.md", "type": "blob"}]})
        if "/contents/README.md" in url:
            return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(b"# Readme\n").decode()})
        raise AssertionError(url)

    client = GitHubApiClient(
        token="TOKEN123",
        webhook_secret="secret",
        doc_allowlist=["README.md", "docs/"],
        transport=httpx.MockTransport(handler),
    )

    snapshot = client.load_pull_request("acme/project", 7)

    assert snapshot.repo == "acme/project"
    assert seen_headers
    assert all(auth == "Bearer TOKEN123" for _, auth, _ in seen_headers)
    assert any(accept == "application/vnd.github.v3.diff" for _, _, accept in seen_headers)


def test_load_pull_request_omits_empty_authorization_header() -> None:
    captured_headers: list[httpx.Headers] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_headers.append(request.headers)
        url = str(request.url)
        if url.endswith("/pulls/7") and request.headers.get("accept") == "application/vnd.github+json":
            return httpx.Response(
                200,
                json={"title": "Public PR", "body": "", "head": {"sha": "head123"}, "base": {"sha": "base123"}},
            )
        if url.endswith("/pulls/7") and request.headers.get("accept") == "application/vnd.github.v3.diff":
            return httpx.Response(200, text="diff")
        if "/pulls/7/files" in url:
            return httpx.Response(200, json=[])
        if "/git/trees/head123" in url:
            return httpx.Response(200, json={"tree": []})
        raise AssertionError(url)

    client = GitHubApiClient(
        token="",
        webhook_secret="secret",
        doc_allowlist=["README.md", "docs/"],
        transport=httpx.MockTransport(handler),
    )

    client.load_pull_request("acme/project", 7)

    assert captured_headers
    assert all("authorization" not in headers for headers in captured_headers)
