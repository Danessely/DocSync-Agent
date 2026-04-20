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
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("DOC_PATH_ALLOWLIST", raising=False)

    settings = Settings.from_env()

    assert settings.github_token == "from-dotenv"
    assert settings.llm_provider == "openai"
    assert settings.doc_path_allowlist == ["README.md", "docs/"]


def test_settings_from_env_does_not_override_exported_env(tmp_path, monkeypatch) -> None:
    (tmp_path / ".env").write_text("GITHUB_TOKEN=from-dotenv\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_TOKEN", "from-env")

    settings = Settings.from_env()

    assert settings.github_token == "from-env"

