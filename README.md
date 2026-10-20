# Enterprise Analytics Copilot

Phase 4 has completed local acceptance. The project now includes the accepted Phase 3 RAG path plus internal, bounded Tool Calling with a read-only PostgreSQL sales tool and a frozen evaluation gate. The final guarded recovery regression passed 3×9 live cases. Existing `/chat` remains on analytics-v2, `/rag/chat` remains separately controlled, and Tool Calling has no public endpoint. This is not a public production release.

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

The API image installs pinned dependencies from requirements.txt and runs as a non-root user. The build includes backend code and the pinned RAG tokenizer assets; .env, evaluation evidence and the local virtual environment are excluded.

For optional host development, use Python 3.12 with analytics-agent-env and install requirements.txt there. Stop the Compose API first (`docker compose stop api`), then run `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765`. The host uses POSTGRES_HOST/PORT from .env; Compose overrides them with postgres:5432 inside the API container.

Documentation lives in docs/. RAG local integration and Phase 4 Tool Calling local acceptance are complete. Tool Calling includes a server-owned registry/executor, an in-memory synthetic tool, a PostgreSQL read-only synthetic tool, bounded provider-independent orchestration, a DeepSeek Tool Calls adapter, source-exposure policy, frozen evaluation versions and a fail-closed database runtime guard for live evaluation. The adapter is disabled by default and no tool flow is connected to a public endpoint. Agent workflows are not implemented. The database role is for isolated local development; production permissions remain a later milestone.

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

Runtime model, timeout/retry, generation and price settings remain centralized in `backend/config.py`. Prompt releases live in `backend/prompt_analytics_v1.py` and the server-owned registry; `backend/prompts.py` builds the active release.

Configuration precedence: process/Compose environment over local `.env`; `LLM_MODEL` over legacy `DEEPSEEK_MODEL` over the catalog default. Empty optional values use defaults. Unsupported provider/model or invalid limits return sanitized 503 / `llm_invalid_config`; no silent provider fallback occurs.

| Environment variable | Default |
|---|---|
| LLM_PROVIDER | deepseek |
| LLM_MODEL | deepseek-flash |
| LLM_TIMEOUT_SECONDS | 30 |
| LLM_MAX_RETRIES | 2 |
| LLM_BACKOFF_SECONDS | 1 |
| LLM_TEMPERATURE | 0.2 (finite, 0–2; explicitly sent) |
| LLM_MAX_OUTPUT_TOKENS | 512 (allowed 1–8192) |

`DEEPSEEK_API_KEY` remains a runtime secret. Thinking stays disabled. Compose forwards overrides while Python owns defaults; recreate the API after changing `.env`. `user_message` must contain 1–8000 characters and cannot be blank.

Phase 2.1–2.2 deliberately removes the earlier public system_message parameter. Requests containing it (including null) return 422. LLM_SYSTEM_MESSAGE is retired and ignored; remove it from local .env if present. Never overwrite existing database credentials or API keys when updating configuration.

```sh
curl -X POST http://127.0.0.1:8765/chat -H 'Content-Type: application/json' \
  -d '{"user_message":"What data do you need to explain a revenue decline?"}'
```

The server assembles the active immutable prompt release, including server-owned instructions and examples, followed by the real user message. Legacy message remains a supported alias for user_message. Unknown fields and ambiguous aliases are rejected. Model and retry settings remain server configuration.

See [prompt architecture](docs/prompt-architecture.md). JSON output is validated locally; two server-owned synthetic few-shot examples guide analytics responses. RAG is exposed separately through `/rag/chat`; Agent is not implemented.

## Phase 1 acceptance

Phase 1 is complete. See [acceptance results](docs/phase1-acceptance.md) for checks and limits. Token counts are validated as nonnegative integers with consistent totals/cache/reasoning counts; invalid usage fails safely with 502. Phase 2 is complete: analytics-v2 is published and active after three gate-v2 passes and Docker verification. analytics-v1 retains its historical 14/15 blocked result.

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

### Prompt versioning (Phase 2.9)

`analytics-v1` freezes the Phase 2.8 production prefix without content changes. The server-owned registry selects the active release. Responses, logs and new eval records include `prompt_version` and `prompt_sha256`; clients cannot select a version. See [publication and rollback](docs/prompt-versioning.md).

### Prompt regression (Phase 2.10)

The unified runner reuses uncertainty/injection cases and adds three positive cases. JSON validity is separate from semantic review; pending/error/fail blocks the release gate. See [run commands and recorded failure](docs/prompt-regression.md). analytics-v1 has not been retuned.

### Phase 2 integration (2.11)

Docker /chat, typed output, version metadata, logging, client boundaries and OpenAPI integration are verified. Integration passing does not clear the existing analytics-v1 semantic gate (14/15, blocked). See [integration checks](docs/phase2-integration.md).

### Phase 2.12 complete

analytics-v2 is published unchanged from the accepted candidate and is active. Three phase2-release-gate-v2 rounds passed; 87 offline tests and Docker smoke checks passed. See [final acceptance](docs/phase2-acceptance.md). Earlier failures and original gate verdicts are preserved. The Phase 3 RAG layer preserves these immutable Phase 2 artifacts.


## Phase 3: first local RAG acceptance

Phase 3.8–3.11 completed local acceptance on 2026-10-15; closeout tests rechecked on 2026-10-16. The single full v5 live regression returned 15/15 valid answers, no schema/grounding/citation/security failures and no core blockers. Quality was 95.56%; minor relevance limitations remain. The independent gate was fixed before that run (hard/core conditions plus a provisional 85% quality threshold). **Repeated-run stability is unverified**; no new paid regression was run for closeout.

- Prompt: `rag-grounded-v5`, SHA `d81ef6b9862fbac388a1a79c48898b68fac0fea1c307241a95f96c243581afb3`.
- Embedding: OpenAI `text-embedding-3-small`, 1536 dimensions. Retrieval uses exact cosine, Top-K 3 and no calibrated distance cutoff.
- Generation: DeepSeek Flash, temperature 0.2, thinking disabled, output reserve 512. Structured output and attribution are validated locally without repair.
- Tests: 174 offline and 31 isolated database tests. Recorded Docker smoke verified health/db/RAG HTTP 200, sources/version/SHA, token/cost logging and client override rejection.
- Historical v1–v4 prompts, SHAs, failed verdicts and frozen evaluation evidence remain intact. Later Phase 4 tool modules do not modify these RAG artifacts.

### Local startup and access scope

Base `docker compose up -d --build --wait` leaves RAG disabled (HTTP 503). To use the accepted local RAG release:

```sh
docker compose -f compose.yaml -f compose.rag-local.yaml up -d --build --wait
```

Keep `OPENAI_API_KEY` and `DEEPSEEK_API_KEY` only in local `.env`. The embedding model/dimensions must match the existing document profile. On a fresh database, run the versioned migrations from the project virtual environment before using RAG:

```sh
python -m scripts.migrate
```

The local override pins a passing release receipt and binds the existing service to `127.0.0.1:8765`. Its server-owned tenant is `local-rag` and document allowlist is **empty by default**. Configure `RAG_TENANT_ID` and `RAG_ALLOWED_DOCUMENT_IDS` in the operator-controlled Compose override for approved, ingested/embedded documents. The endpoint cannot ingest files or grant itself document access. This is not a multiuser authentication/authorization deployment.

POST `/rag/chat` accepts only `{"query":"..."}`. It returns the separate `rag-response-v1` contract: structured answer, citations, source metadata, generation usage/version/cost, token budget and context SHA. An empty authorized corpus yields no retrieved evidence, rather than unrestricted access. Generation still incurs API usage; run live requests deliberately. `/docs` documents both endpoints. For host RAG development, use `backend.rag_app:app`; the original `backend.main:app` remains the unchanged chat entrypoint.

### Non-paid verification and evidence

```sh
python -m unittest discover -s tests -v
python -m unittest discover -s tests/db -v
```

Database tests require local PostgreSQL and create/drop only uniquely named temporary databases. These commands do not call paid embedding/generation APIs. Live regression runners require separate deliberate execution and must not be used as routine tests.

See [Phase 3 roadmap](docs/phase3-roadmap.md), [final local acceptance](docs/phase3-final-acceptance.md), [Docker smoke evidence](docs/phase3-local-acceptance.json), and [gate result](eval/rag/v5-release-gate-result.json). Full raw/reviewed v5 evidence is under `eval/rag/`; private rejected-response diagnostics, backups, credentials and caches are excluded from Git.

## Phase 4: internal Tool Calling — local acceptance complete

Phase 4.1 defines strict Pydantic call/result contracts, a server-owned tool registry and authorization policy, and a fail-closed executor with safe metadata logs. The first registered tool, `get_sales_summary`, calculates totals, order count and average order value from fixed synthetic data using exact decimal arithmetic. Phase 4.2 keeps that tool and adds `get_postgres_sales_summary`, backed by versioned synthetic rows in `analytics.sales_orders`, a fixed parameterized query and an explicit read-only transaction. Database AOV is `null` when no orders match and `"0.00"` only when matched orders have a zero average. Phase 4.3 adds strict provider decision/orchestration result contracts and a deterministic flow capped at one tool execution and two Provider turns. Phase 4.4 implements the official DeepSeek `tool_calls`/`tool_call_id` exchange, safe usage/cost telemetry and a default-off internal runtime. Tool output is returned only in an explicit untrusted-data envelope.



Phase 4.5 preserves three evaluator generations and every historical verdict. The original v1 live run exposed evaluator false positives. Evaluator v2 separated authorization from source policy and corrected numeric classification; evaluator v3 added strict but Markdown-tolerant business-metric grounding. A first v3 live run produced 7/9 per round because Docker/PostgreSQL was unavailable; that Gate FAIL remains immutable. After infrastructure recovery, a runtime guard checked the Agent-side database and real PostgreSQL tool before every PostgreSQL-dependent provider interaction. The independent guarded recovery ran the unchanged frozen v3 Suite, Evaluator, Gate, Prompt and DeepSeek configuration three times: **27/27 cases PASS, Gate PASS**, with 12/12 database guards healthy, zero retries and estimated cost USD 0.003135582. Final non-paid verification passed 285 offline and 44 database tests.

This is local engineering acceptance. There is no public Tool API, arbitrary SQL/code execution or open-ended agent loop. `TOOL_CALLING_ENABLED=false` by default, and no existing route loads the tool runtime. Calls are possible only from trusted application code using the `analytics-copilot` principal. Production work still needs real identity/RBAC, a least-privilege database role, durable audit/usage storage, secret-manager integration, availability monitoring, deployment controls and human approval policy for consequential tools. The current synthetic dataset, provisional quality threshold and three local live rounds do not establish production reliability.

Apply migrations and seed the local synthetic table explicitly with `python -m scripts.migrate` and `python -m scripts.seed_sales`. See the [Tool Calling architecture](docs/phase4-tool-calling.md), [PostgreSQL tool design](docs/phase4-postgres-tools.md), [orchestration design](docs/phase4-orchestration.md), [DeepSeek adapter](docs/phase4-deepseek-tool-calling.md), [Phase 4.5 evaluation design](docs/phase4-tool-calling-evaluation.md), [historical v3 FAIL](docs/phase4-tool-calling-live-v3-acceptance.md), and [final guarded recovery acceptance](docs/phase4-tool-calling-live-v3-recovery-acceptance.md).

## Phase 5.1: bounded multi-step planning foundation

Phase 5.1 adds an internal, provider-independent planning package with strict plans, ordered step dependencies, validated state snapshots and server-owned step/tool/timeout/budget limits. Its deterministic demonstration loads Q1 and Q2 through the existing authorized PostgreSQL tool, verifies a comparable source scope, then calculates absolute and percentage change in Python with `Decimal`. The runtime and `MULTI_STEP_PLANNING_ENABLED` flag default to off, and no public planning endpoint exists. Existing single-tool orchestration, `/chat` and `/rag/chat` are unchanged.

The current Fake Provider and PostgreSQL verification do not call a paid model. Local acceptance passed 297 offline and 46 isolated database tests. Planning state is in memory, timeout enforcement is cooperative at step boundaries, and production identity, durable checkpoints, resumability, cancellation and human approval remain future work. See the [Phase 5.1 design](docs/phase5-bounded-planning.md).

Phase 5.2 adds deterministic plan dependency, cycle, order and task-binding validation plus guarded step/agent state transitions. Tool exceptions and malformed tool results fail closed. Checkpoint persistence, task recovery and idempotency are specified in [the Phase 5.2 design](docs/phase5-plan-validation-and-recovery.md), but no production schema or resume runtime is created in this phase.
