# Learning log

- Confirmed the distinction between API responsiveness and database connectivity.
- Initialized project structure and isolated Python 3.12 environment.
- Implemented GET /health with FastAPI and Uvicorn; pinned dependencies.
- Port 8000 was occupied; used 8765 without disturbing the existing service.
- Verified a real HTTP request to port 8765 returned 200 and {"status":"ok"}; stopped the test server afterward.
- Docker 29.7.2 and Compose 5.5.1 verified. PostgreSQL 17.11 container healthy.
- Verified real HTTP 200 responses from /health and /health/db; the latter executed SELECT 1 and returned 1.
- Verified database errors map to generic HTTP 503 without exposing connection details.
- Completed Phase 0 containerization: Compose starts PostgreSQL and FastAPI; both report healthy.
- Real HTTP requests to the containerized API returned 200 for /health and /health/db, with SELECT 1 returning 1.
- Confirmed the API runs as non-root and its image contains neither .env nor analytics-agent-env.
- Phase 1: direct real deepseek-flash call returned OK with 58 total tokens.
- Container POST /chat returned HTTP 200, OK, 9 prompt + 1 completion = 10 tokens, provider latency 1151 ms (single smoke test, not a benchmark).
- Eight offline tests passed; blank HTTP input returned 422; /health and /health/db remained HTTP 200.
- Added provider abstraction and placeholder .env.example; no new dependencies.
- Phase 1 Task 1 complete: classified sanitized provider errors, fixed wrapped timeouts and interrupted/malformed responses; 12 offline tests pass, including ASGI error-response verification. No paid API request needed for this task.
- Phase 1 Tasks 2–3 complete: validated network timeout and bounded HTTP retries with jitter; correlated attempt/request usage and latency logs; versioned cache-aware peak/off-peak USD cost estimates.
- 19 offline tests pass, covering retries, deterministic failures, config bounds, price boundaries, log sanitization and total latency.
- Real container /chat verification: HTTP 200, 10 tokens, 1125 ms, retry_count=0, estimated USD 0.00000195; both health endpoints HTTP 200. Single sample, not a benchmark.
- Phase 1 Tasks 4–5 complete: centralized provider/model/retry/generation/price configuration with validated overrides and a provider adapter registry (DeepSeek only).
- Added user_message and optional system_message, simple default instruction, legacy message compatibility, and ambiguity/blank validation.
- 26 offline tests pass. Real container call with separate roles returned HTTP 200, OK, 12 tokens, 1080 ms; OpenAPI fields and HTTP 422 invalid-input checks passed; health endpoints remain HTTP 200.
- Phase 1 closeout (2026-09-14): 31 tests passed; strict token consistency fix; Docker down/up recreation, two real chat requests, health and credential/image isolation checks passed. See phase1-acceptance.md. Stop before Phase 2.

- Phase 2.1–2.2 (2026-09-20): added provider-independent prompt assembly and server-owned Enterprise Analytics Copilot System Prompt v1 in backend/prompts.py. Removed public system_message and retired LLM_SYSTEM_MESSAGE; preserved user_message/legacy message. No new dependencies.
- Verification: 34 offline tests passed; Compose rebuild/start succeeded; /health and /health/db returned 200; system override returned 422. One real /chat request returned 200, 239 tokens, 1526 ms, zero retries; the answer explicitly acknowledged missing revenue data. This smoke test is not a factuality or injection-resistance guarantee.
- Updated the complete Phase 2 Prompt Engineering roadmap; only 2.1 and 2.2 are complete. Stop before 2.3.

- Phase 2.3 (2026-09-20): added a server-owned ordinary-text output contract with summary, facts, interpretation and limitations. Facts require supplied evidence or reproducible calculations; hypotheses belong in interpretation; missing information belongs in limitations. Answer API shape unchanged; no new dependencies or JSON enforcement.
- 35 tests passed; Docker rebuild succeeded; two real HTTP 200 responses correctly separated provided facts/calculation from uncertain causes or missing data. See output-contract.md for smoke-check details and limits. Stop before 2.4.

- Phase 2.4 (2026-09-20): enabled DeepSeek JSON object mode; centralized AnalyticsAnswer schema and strict parser; answer is now a validated object. Missing/wrong/extra fields, invalid/ambiguous JSON and incomplete generation fail safely without repair or retry. No dependencies added.
- Verification: 38 offline tests passed, including failure sanitization/no retry and OpenAPI schema; Docker rebuild passed; live /docs returned 200 with the nested schema; one real DeepSeek call using explicitly synthetic toy-sales data returned HTTP 200, 847 total tokens and finish_reason=stop, with all four fields validated. Formatting/type checks do not validate factual correctness. Stop before 2.5.

- Phase 2.5: added two server-owned, provider-independent synthetic examples for unverified cause attribution and unsupported churn premises. Both assistant messages validate as AnalyticsAnswer JSON. 41 tests pass, including final-user ordering, request isolation and client override rejection. No dependencies or paid model calls added; dedicated hallucination evaluation remains unstarted. Stop before 2.6.
