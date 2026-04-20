from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


def _split_csv(value: str | None, default: list[str]) -> list[str]:
    if not value:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _load_dotenv(path: str | Path = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


class Settings(BaseModel):
    github_webhook_secret: str = ""
    github_token: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_timeout_sec: int = 10
    llm_provider: str = "mock"
    llm_model: str = "mock-model"
    llm_timeout_sec: int = 20
    llm_api_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    doc_path_allowlist: list[str] = Field(default_factory=lambda: ["README.md", "docs/"])
    max_diff_lines: int = 1000
    max_doc_candidates: int = 3
    max_changed_doc_files: int = 3
    max_patch_lines: int = 200
    publish_mode: str = "comment_only"
    dry_run: bool = False
    min_confidence: float = 0.6
    log_level: str = "INFO"
    session_store_path: str = ".docsync/session_store.json"
    docs_validation_command: str = ""
    docs_validation_timeout_sec: int = 30
    github_max_retries: int = 2
    github_backoff_base_sec: float = 0.5

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv()
        values: dict[str, Any] = {
            "github_webhook_secret": os.getenv("GITHUB_WEBHOOK_SECRET", ""),
            "github_token": os.getenv("GITHUB_TOKEN", ""),
            "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
            "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
            "telegram_timeout_sec": int(os.getenv("TELEGRAM_TIMEOUT_SEC", "10")),
            "llm_provider": os.getenv("LLM_PROVIDER", "mock"),
            "llm_model": os.getenv("LLM_MODEL", "mock-model"),
            "llm_timeout_sec": int(os.getenv("LLM_TIMEOUT_SEC", "20")),
            "llm_api_base_url": os.getenv("LLM_API_BASE_URL", "https://api.openai.com/v1"),
            "llm_api_key": os.getenv("LLM_API_KEY", ""),
            "doc_path_allowlist": _split_csv(
                os.getenv("DOC_PATH_ALLOWLIST"),
                ["README.md", "docs/"],
            ),
            "max_diff_lines": int(os.getenv("MAX_DIFF_LINES", "1000")),
            "max_doc_candidates": int(os.getenv("MAX_DOC_CANDIDATES", "3")),
            "max_changed_doc_files": int(os.getenv("MAX_CHANGED_DOC_FILES", "3")),
            "max_patch_lines": int(os.getenv("MAX_PATCH_LINES", "200")),
            "publish_mode": os.getenv("PUBLISH_MODE", "comment_only"),
            "dry_run": os.getenv("DRY_RUN", "false").lower() == "true",
            "min_confidence": float(os.getenv("MIN_CONFIDENCE", "0.6")),
            "log_level": os.getenv("LOG_LEVEL", "INFO"),
            "session_store_path": os.getenv("SESSION_STORE_PATH", ".docsync/session_store.json"),
            "docs_validation_command": os.getenv("DOCS_VALIDATION_COMMAND", ""),
            "docs_validation_timeout_sec": int(os.getenv("DOCS_VALIDATION_TIMEOUT_SEC", "30")),
            "github_max_retries": int(os.getenv("GITHUB_MAX_RETRIES", "2")),
            "github_backoff_base_sec": float(os.getenv("GITHUB_BACKOFF_BASE_SEC", "0.5")),
        }
        return cls(**values)
