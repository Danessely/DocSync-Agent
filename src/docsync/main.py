from __future__ import annotations

import json
import logging

import uvicorn
from fastapi import FastAPI, HTTPException, Request

from .adapters.github import GitHubApiClient
from .adapters.llm import ChatOpenAILLMClient, MockLLMClient
from .adapters.telegram import TelegramBotClient, extract_session_id
from .config import Settings
from .graph.workflow import DocSyncWorkflow
from .models import TelegramReply
from .state_store import FileSessionStore, InMemorySessionStore


def _build_llm_client(settings: Settings):
    if settings.llm_provider == "mock":
        return MockLLMClient()
    return ChatOpenAILLMClient(settings)


def _build_telegram_client(settings: Settings):
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return None
    return TelegramBotClient(
        bot_token=settings.telegram_bot_token,
        chat_id=settings.telegram_chat_id,
        timeout=settings.telegram_timeout_sec,
    )


def _build_state_store(settings: Settings):
    if not settings.session_store_path:
        return InMemorySessionStore()
    return FileSessionStore(settings.session_store_path)


def create_app(
    settings: Settings | None = None,
    github_client=None,
    llm_client=None,
    telegram_client=None,
    state_store=None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

    github_client = github_client or GitHubApiClient(
        token=settings.github_token,
        webhook_secret=settings.github_webhook_secret,
        doc_allowlist=settings.doc_path_allowlist,
    )
    llm_client = llm_client or _build_llm_client(settings)
    telegram_client = telegram_client or _build_telegram_client(settings)
    state_store = state_store or _build_state_store(settings)
    workflow = DocSyncWorkflow(
        settings,
        github_client,
        llm_client,
        telegram_client=telegram_client,
        state_store=state_store,
    )

    app = FastAPI(title="DocSync Agent")
    app.state.settings = settings
    app.state.workflow = workflow
    app.state.github_client = github_client
    app.state.telegram_client = telegram_client
    app.state.state_store = state_store

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

    @app.post("/webhooks/telegram")
    async def telegram_webhook(request: Request) -> dict:
        if app.state.telegram_client is None:
            raise HTTPException(status_code=503, detail="telegram_not_configured")

        payload = json.loads(await request.body())
        parsed_reply = app.state.telegram_client.parse_reply(payload)
        reply = TelegramReply.model_validate(parsed_reply) if parsed_reply is not None else None
        if reply is None:
            return {"status": "ignored", "reason": "unsupported_update"}

        session_id = extract_session_id(reply)
        if session_id is None:
            return {"status": "ignored", "reason": "missing_session_id"}

        try:
            result = workflow.resume_from_clarification(session_id, reply.text)
        except KeyError:
            return {"status": "ignored", "reason": "unknown_session"}

        return {
            "status": result.get("outcome", "unknown"),
            "stage": result.get("stage", "unknown"),
            "session_id": session_id,
            "error_code": result.get("error_code"),
        }

    return app


def main() -> None:
    settings = Settings.from_env()
    app = create_app(settings=settings)
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
