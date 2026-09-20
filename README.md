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

Copy `.env.example` to `.env` only for a new setup. For an existing setup, preserve database credentials and add `DEEPSEEK_API_KEY` locally. Never share or commit its value. `LLM_MODEL` defaults to `deepseek-flash`; the legacy `DEEPSEEK_MODEL` remains a fallback.

```sh
docker compose up -d --build --wait
curl -X POST http://127.0.0.1:8765/chat -H 'Content-Type: application/json' -d '{"message":"Hello"}'
```

The response includes `answer`, `provider`, `model`, `usage`, `latency_ms` and `finish_reason`. Usage includes input/output/total tokens, cache hit/miss tokens and reasoning tokens when supplied by DeepSeek. Missing optional counters are null, not fabricated zeroes. Logs record usage metadata without prompts, answers or API keys. These logs are not a billing ledger or durable usage database.

This is a single-turn, non-streaming call to the official OpenAI-compatible `https://api.deepseek.com/chat/completions` endpoint. Thinking is disabled and output is limited to 512 tokens. Incomplete generation (including `finish_reason=length`) fails with 502 / `llm_invalid_output`. The default network timeout is 30 seconds per blocking operation; retry configuration is documented below. Input is limited to 8000 characters and blank messages return 422. Provider configuration/rate/balance errors return 503, other upstream failures 502, and timeouts 504, without upstream error details.

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
| 502 | llm_invalid_response | Malformed upstream envelope or invalid/missing usage |
| 502 | llm_invalid_output | Invalid JSON/schema or incomplete generation; no repair/retry |
| 504 | llm_timeout | Direct or wrapped network timeout |

Messages are locally defined; upstream bodies and credentials are never returned. Retry behavior is documented below.
[DeepSeek error reference](https://api-docs.deepseek.com/quick_start/error_codes/)

### Timeout and retry

- `LLM_TIMEOUT_SECONDS`: per-blocking-operation network timeout, default 30, range (0,120]. This is not a hard end-to-end deadline.
- `LLM_MAX_RETRIES`: default 2 (3 attempts total), range 0–3.
- `LLM_BACKOFF_SECONDS`: default 1, range (0,5]. Delay is min(8, base * 2^retry_index) plus 0–25% jitter, at most 10 seconds per wait.
- Retry only upstream HTTP 429, 500, 502, 503 and 504. Authentication, access, balance, invalid request and malformed responses are not retried.
- Network timeouts and disconnects are not automatically retried because completion and charging may already have occurred. HTTP retries can also incur upstream usage; no exactly-once guarantee is claimed.
- Invalid retry configuration returns HTTP 503 / `llm_invalid_config` without exposing environment values. Restart/recreate the API after configuration changes.

### Usage, latency and estimated cost

Logs emit `llm_attempt` for each attempt and `llm_request` once at completion/failure. JSON records contain request_id, provider, model, input/output/total tokens, latency_ms, retry_count and status. Attempt latency excludes backoff; request latency includes attempts and waits. Request records summarize attempts and must not be summed with attempt records.

The response adds request_id, retry_count, estimated_cost (decimal string in USD), pricing_version, pricing_tier and cost_complete. Token counts and estimated_cost describe the successful attempt only. Failed attempts have unknown usage/cost (null), so any retried request has cost_complete=false. Missing cache counts or unknown model prices also produce null cost, never an invented zero.

Rates and peak/off-peak rules are maintained in `backend/config.py`, with source URL and snapshot version. Rates are USD per million tokens. Estimates use cache-hit input, cache-miss input and output counts; reasoning tokens are already part of output and are not charged twice. The rate tier is selected using the UTC start time of the successful attempt. Requests crossing a rate boundary may differ from provider billing. These are estimates for reported usage, not a billing ledger or guarantee of the final charged amount.

[Official price source](https://api-docs.deepseek.com/quick_start/pricing/)

### Model configuration and server-owned prompts

Runtime model, timeout/retry, generation and price settings remain centralized in `backend/config.py`. Prompt content now lives separately in `backend/prompts.py`.

Configuration precedence: process/Compose environment over local `.env`; `LLM_MODEL` over legacy `DEEPSEEK_MODEL` over the catalog default. Empty optional values use defaults. Unsupported provider/model or invalid limits return sanitized 503 / `llm_invalid_config`; no silent provider fallback occurs.

| Environment variable | Default |
|---|---|
| LLM_PROVIDER | deepseek |
| LLM_MODEL | deepseek-flash |
| LLM_TIMEOUT_SECONDS | 30 |
| LLM_MAX_RETRIES | 2 |
| LLM_BACKOFF_SECONDS | 1 |
| LLM_MAX_OUTPUT_TOKENS | 512 (allowed 1–8192) |

`DEEPSEEK_API_KEY` remains a runtime secret. Thinking stays disabled. Compose forwards overrides while Python owns defaults; recreate the API after changing `.env`. `user_message` must contain 1–8000 characters and cannot be blank.

Phase 2.1–2.2 deliberately removes the earlier public system_message parameter. Requests containing it (including null) return 422. LLM_SYSTEM_MESSAGE is retired and ignored; remove it from local .env if present. Never overwrite existing database credentials or API keys when updating configuration.

```sh
curl -X POST http://127.0.0.1:8765/chat -H 'Content-Type: application/json' \
  -d '{"user_message":"What data do you need to explain a revenue decline?"}'
```

The server assembles System Prompt v1 followed by the user message. Legacy message remains a supported alias for user_message. Unknown fields and ambiguous aliases are rejected. Model and retry settings remain server configuration.

See [prompt architecture](docs/prompt-architecture.md). JSON output is validated locally; two server-owned synthetic few-shot examples guide analytics responses. RAG and Agent are not implemented.

## Phase 1 acceptance

Phase 1 is complete. See [acceptance results](docs/phase1-acceptance.md) for checks and limits. Token counts are validated as nonnegative integers with consistent totals/cache/reasoning counts; invalid usage fails safely with 502. Phase 2.1–2.8 are complete; later Phase 2 tasks have not started.

### Structured output (Phase 2.4)

`answer` is now an object with required `summary: string`, `facts: list[string]`, `interpretation: list[string]`, and `limitations: list[string]`. Clients expecting a string must adapt. DeepSeek JSON mode is enabled; the server strictly parses and validates before returning. Invalid output safely returns 502 without repair or retry. `/docs` shows the nested response schema. See [contract and enforcement limits](docs/output-contract.md).

### Few-shot examples (Phase 2.5)

Two provider-independent examples demonstrate fact/hypothesis separation and insufficient evidence. They precede the real user message and use synthetic data only. See [example design and tests](docs/few-shot-examples.md).

### Uncertainty check (Phase 2.6)

Six synthetic cases cover unsupported causes/premises, contradicted premises, supplied hypotheses, missing metrics and unavailable tools. Baseline 5/6; after a general prompt correction, final 6/6 in one run. See [rubrics, evidence and limitations](docs/uncertainty-evaluation.md).

### Prompt boundaries (Phase 2.7)

User/quoted instructions cannot acquire system authority; internal prompt disclosure and conflicting output requests are constrained in the server prompt. Six synthetic real-call checks: baseline 4/6, final 6/6 after a generic correction. This does not solve prompt injection. See [evidence and limitations](docs/injection-boundaries.md).

### Context efficiency (Phase 2.8)

Measured context components and compared a schema-whitespace candidate using both eval sets. Production prompt retained: minor input savings did not establish quality/stability or total-cost improvement. See [evidence and decision](docs/context-efficiency.md).
