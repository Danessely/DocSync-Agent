from __future__ import annotations

from pathlib import Path

from docsync.config import Settings


def test_settings_from_env_loads_dotenv_file(tmp_path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "GITHUB_TOKEN=from-dotenv",
                "LLM_PROVIDER=openai",
                "DOC_PATH_ALLOWLIST=README.md,docs/",
                "SESSION_STORE_PATH=.docsync/custom-store.json",
                "DOCS_VALIDATION_COMMAND=mkdocs build --strict",
                "DOCS_VALIDATION_TIMEOUT_SEC=45",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("DOC_PATH_ALLOWLIST", raising=False)
    monkeypatch.delenv("SESSION_STORE_PATH", raising=False)
    monkeypatch.delenv("DOCS_VALIDATION_COMMAND", raising=False)
    monkeypatch.delenv("DOCS_VALIDATION_TIMEOUT_SEC", raising=False)

    settings = Settings.from_env()

    assert settings.github_token == "from-dotenv"
    assert settings.llm_provider == "openai"
    assert settings.doc_path_allowlist == ["README.md", "docs/"]
    assert settings.session_store_path == ".docsync/custom-store.json"
    assert settings.docs_validation_command == "mkdocs build --strict"
    assert settings.docs_validation_timeout_sec == 45


def test_settings_from_env_does_not_override_exported_env(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("GITHUB_TOKEN=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")

    settings = Settings.from_env()

    assert settings.github_token == "from-env"
