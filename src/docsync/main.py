from __future__ import annotations

import json
import logging

import uvicorn
from fastapi import FastAPI, HTTPException, Request

from .adapters.github import GitHubApiClient
from .adapters.llm import ChatOpenAILLMClient, MockLLMClient
from .config import Settings
from .graph.workflow import DocSyncWorkflow


def _build_llm_client(settings: Settings):
    if settings.llm_provider == "mock":
        return MockLLMClient()
    return ChatOpenAILLMClient(settings)


def create_app(
    settings: Settings | None = None,
    github_client=None,
    llm_client=None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    github_client = github_client or GitHubApiClient(
        token=settings.github_token,
        webhook_secret=settings.github_webhook_secret,
        doc_allowlist=settings.doc_path_allowlist,
    )
    llm_client = llm_client or _build_llm_client(settings)
    workflow = DocSyncWorkflow(settings, github_client, llm_client)

    app = FastAPI(title="DocSync Agent")
    app.state.settings = settings
    app.state.workflow = workflow
    app.state.github_client = github_client

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> dict:
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        if not app.state.github_client.verify_webhook_signature(body, signature):
            raise HTTPException(status_code=401, detail="invalid_signature")

        payload = json.loads(body)
        result = workflow.run_once(payload)
        return {
            "status": result.get("outcome", "unknown"),
            "stage": result.get("stage", "unknown"),
            "error_code": result.get("error_code"),
        }

    return app


def main() -> None:
    settings = Settings.from_env()
    app = create_app(settings=settings)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
