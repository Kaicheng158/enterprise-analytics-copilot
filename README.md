# Enterprise Analytics Copilot

Phase 1: a single-provider DeepSeek chat API, with FastAPI and PostgreSQL running in Docker Compose.

## Start

Start Docker Desktop. From the repository root, create a local `.env` with your own password:

```text
POSTGRES_USER=analytics
POSTGRES_DB=analytics
POSTGRES_PASSWORD=<your-local-password>
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

The existing local `.env` can be reused. Never commit it. Credentials initialize a new database volume; editing them does not change existing database credentials.

```sh
docker compose up -d --build --wait
```

The API waits for PostgreSQL to be healthy. Ensure port 8765 is not occupied by a separately running Uvicorn process.

## Verify

```sh
curl -i http://127.0.0.1:8765/health
curl -i http://127.0.0.1:8765/health/db
docker compose ps
```

Both requests should return HTTP 200:

- `/health`: `{"status":"ok"}`
- `/health/db`: `{"status":"ok","database":"connected","result":1}` (executes SELECT 1).

Database errors return HTTP 503. Both published ports bind to localhost only.

## Stop and restart

```sh
docker compose down
docker compose up -d --wait
```

The named database volume is preserved. Do not add `--volumes` unless intentionally deleting database data.

## Development

The API image installs pinned dependencies from requirements.txt and runs as a non-root user. Only backend Python files and requirements are included in its build context; .env and the local virtual environment are excluded.

For optional host development, use Python 3.12 with analytics-agent-env and install requirements.txt there. Stop the Compose API first (`docker compose stop api`), then run `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765`. The host uses POSTGRES_HOST/PORT from .env; Compose overrides them with postgres:5432 inside the API container.

Documentation lives in docs/. RAG, Tool Calling and Agent features are not implemented. The database role is for isolated local development; production permissions are a later milestone.

## Phase 1: DeepSeek chat

Copy `.env.example` to `.env` only for a new setup. For an existing setup, preserve database credentials and add `DEEPSEEK_API_KEY` locally. Never share or commit its value. `DEEPSEEK_MODEL` defaults to `deepseek-flash`.

```sh
docker compose up -d --build --wait
curl -X POST http://127.0.0.1:8765/chat -H 'Content-Type: application/json' -d '{"message":"Hello"}'
```

The response includes `answer`, `provider`, `model`, `usage`, `latency_ms` and `finish_reason`. Usage includes input/output/total tokens, cache hit/miss tokens and reasoning tokens when supplied by DeepSeek. Missing optional counters are null, not fabricated zeroes. Logs record usage metadata without prompts, answers or API keys. These logs are not a billing ledger or durable usage database.

This is a single-turn, non-streaming call to the official OpenAI-compatible `https://api.deepseek.com/chat/completions` endpoint. Thinking is disabled and output is limited to 512 tokens. `finish_reason=length` means the answer was truncated. The network timeout is 30 seconds per blocking operation; there are no automatic retries. Input is limited to 8000 characters and blank messages return 422. Provider configuration/rate/balance errors return 503, other upstream failures 502, and timeouts 504, without upstream error details.

No new package is needed: the adapter uses Python's standard HTTP library. Business routes depend on an LLMProvider protocol, not DeepSeek HTTP details. Only DeepSeek is implemented.

Run offline tests from the activated project virtual environment:

```sh
python -m unittest discover -s tests -v
```

[DeepSeek Chat Completions documentation](https://api-docs.deepseek.com/api/create-chat-completion/)

### LLM error contract

Provider failures return `{"detail":{"code":"llm_rate_limited","message":"LLM provider rate limit reached"}}` (example).
This replaces the earlier string-valued provider `detail`. Request validation remains FastAPI HTTP 422.

| HTTP | Code | Meaning |
|---|---|---|
| 503 | llm_not_configured | Missing local API key |
| 503 | llm_authentication_failed / llm_access_denied | Upstream credential or access failure |
| 503 | llm_insufficient_balance | Upstream balance unavailable |
| 503 | llm_rate_limited | Upstream rate limit |
| 502 | llm_request_rejected | Upstream rejects request format/parameters |
| 502 | llm_upstream_error | Other upstream HTTP failure |
| 502 | llm_connection_failed | Connection failure or interrupted response |
| 502 | llm_invalid_response | Malformed response, missing usage or empty answer |
| 504 | llm_timeout | Direct or wrapped network timeout |

Messages are locally defined; upstream bodies and credentials are never returned. No retry behavior is introduced by this task.
[DeepSeek error reference](https://api-docs.deepseek.com/quick_start/error_codes/)
