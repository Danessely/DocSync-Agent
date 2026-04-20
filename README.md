# DocSync Agent

Агентный сервис для автоматического обновления документации на основе изменений в коде.

## Локальный запуск

### Подготовка окружения

Скопируйте шаблон переменных окружения и заполните нужные значения:

```bash
cp .env.sample .env
```

Для локального прогона снапшота достаточно оставить `LLM_PROVIDER=mock`.

Для webhook-режима необходимо [настроить webhook](https://docs.github.com/ru/webhooks/using-webhooks/creating-webhooks#creating-a-repository-webhook) с токеном на Pull Request в репозитории, понадобятся переменные:

- `GITHUB_WEBHOOK_SECRET` — для проверки запросов
- `GITHUB_TOKEN`

Для отправки уточняющих вопросов через Telegram заполните:

- `TELEGRAM_BOT_TOKEN` — токен бота
- `TELEGRAM_CHAT_ID` — ID пользователя, которому нужно отправлять сообщения

Для работы с LLM OpenAI-like API, также заполните:

- `LLM_PROVIDER`
- `LLM_MODEL`
- `LLM_API_KEY`

Режим публикации результата задаётся через `PUBLISH_MODE`:

- `comment_only` — оставить comment с patch preview
- `commit_patch` — закоммитить doc-изменения прямо в head branch PR

Локальное состояние сессий и дедупликация по `head_sha` сохраняются в файл, путь задаётся через `SESSION_STORE_PATH`.
По умолчанию используется `.docsync/session_store.json`.

Для дополнительной технической проверки документации можно задать `DOCS_VALIDATION_COMMAND`.
По умолчанию эта проверка выключена, и MkDocs для работы PoC не нужен.

Если указать, например, `DOCS_VALIDATION_COMMAND="mkdocs build --strict"`, тогда `mkdocs` должен быть установлен и доступен в `PATH`.
Команда выполняется во временной директории с текущим snapshot документации и proposed patch.

Для GitHub API доступны ограниченные ретраи на временных ошибках:

- `GITHUB_MAX_RETRIES`
- `GITHUB_BACKOFF_BASE_SEC`

После исчерпания ретраев publish-запрос завершается состоянием `failed_publish`.

Для отслеживания и тресинга запросов и работы сервиса рекомендуется подключить LangSmith:

- `LANGSMITH_TRACING=true`
- `LANGSMITH_ENDPOINT`
- `LANGSMITH_API_KEY`
- `LANGSMITH_PROJECT`

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

### Eval harness

В репозитории есть минимальный набор для оценки в `evals/cases/`.
Запуск всего набора:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m docsync.evals evals/cases
```

JSON-режим:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m docsync.evals evals/cases --json
```

### Webhook-сервер

Запуск приложения:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m docsync.main
```

После старта доступны:

- `GET /health`
- `POST /webhooks/github`
