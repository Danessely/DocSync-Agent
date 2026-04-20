# DocSync Agent

Агентный сервис для автоматического обновления документации на основе изменений в коде.

## Локальный запуск

### Подготовка окружения

Скопируйте шаблон переменных окружения и заполните нужные значения:

```bash
cp .env.sample .env
```

Для локального прогона snapshot runner достаточно оставить `LLM_PROVIDER=mock`.

Для webhook-режима понадобятся:

- `GITHUB_WEBHOOK_SECRET`
- `GITHUB_TOKEN`

Если используется реальный LLM provider вместо mock, также заполните:

- `LLM_PROVIDER`
- `LLM_MODEL`
- `LLM_API_KEY`

### Проверка тестами

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run pytest
```

### Ручной прогон по сохранённому snapshot

В репозитории есть пример snapshot-файла:

[sample_snapshot.json](/root/projects/DocSync-Agent/tests/fixtures/sample_snapshot.json)

Запуск:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m docsync.manual tests/fixtures/sample_snapshot.json
```

JSON-режим:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m docsync.manual tests/fixtures/sample_snapshot.json --json
```

### Webhook-сервер

Запуск приложения:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m docsync.main
```

После старта доступны:

- `GET /health`
- `POST /webhooks/github`

На текущем этапе PoC безопаснее всего проверять бизнес-логику через тесты или snapshot runner. Webhook-режим рассчитан на реальный GitHub PR event и реальный GitHub API token.
