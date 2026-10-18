# Roadmap

## Overall stages

0. Engineering foundation — complete: FastAPI + PostgreSQL in Compose.
1. LLM API and prompts — complete, DeepSeek only.
2. Prompt Engineering — complete: analytics-v2 published and active after three gate-v2 passes and Docker acceptance.
3. RAG — 3.1–3.11 first local acceptance complete; repeated-run stability and public deployment remain future work. See [Phase 3 roadmap](phase3-roadmap.md).

Later-stage SQL/tool/agent numbering below is provisional and will be revised separately.
4. Analysis tools and controlled agent — Phase 4 local acceptance complete through 4.5: registered read-only tools, bounded orchestration, default-off DeepSeek Tool Calls, versioned evaluation and frozen v3 Gate PASS. No public tool API or open-ended agent loop.
5. LangGraph workflows — not started.
6. Evaluation — not started.
7. Deployment — not started.
8. Enterprise permissions, security and human review — not started.

## Phase 1 engineering checklist

Completed baseline: real DeepSeek API integration, provider abstraction, POST /chat, request/response schemas, reported token usage, basic successful-call latency logging, model name from environment.

1. [x] Error handling: stable error codes; sanitized authentication/balance/rate-limit/upstream errors; wrapped timeouts, broken connections and malformed responses; API error-contract regression tests.
2. [x] Timeout/retry: configurable timeouts; bounded attempts and backoff; explicit retryable errors; avoid retries for authentication/balance/invalid requests; tests of attempt counts and limits. Completed: validated timeout/retry configuration, bounded HTTP retries with exponential backoff/jitter; ambiguous network failures are not retried.
3. [x] Usage/latency/cost logging: request correlation, success/failure records, per-attempt versus total timing, versioned model pricing and currency for estimates, cache accounting, unknown usage/cost handling. Estimates must not be presented as billed amounts; no billing ledger or quota implementation.
4. [x] Model config: centralized validated DeepSeek model, output limits and generation settings; update env example and Compose configuration. Completed: central validated config/catalog, price snapshot, environment precedence and one registered adapter.
5. [x] Basic system/user messages: configurable system instruction and validated user message; no conversation memory or tools.
6. [x] Phase 1 closeout: full regression suite, bounded real-call verification, configuration/startup checks, updated README and completion criteria.

Work rule: execute one task, run relevant tests, update documentation, commit and push, then stop. Phase 1 accepted on 2026-09-14. All six engineering tasks complete. Phase 2 now follows the user-defined Prompt Engineering roadmap.

## Future direction (not implemented)

Multiple providers/models and quality/latency/cost comparisons; platform-managed keys, BYOK, durable usage tracking, user quota and billing. Only the provider boundary is reserved today. Do not begin RAG, Tool Calling, Agent or LangGraph during Phase 1.

## Phase 2 — Prompt Engineering (supersedes the former RAG phase label)

- [x] 2.1 Prompt architecture: provider-independent message assembly in backend/prompts.py.
- [x] 2.2 Server-owned System Prompt v1: role, goal, data boundaries and insufficient-information instruction; public overrides rejected.
- [x] 2.3 Output contract: four ordinary-text sections (summary/facts/interpretation/limitations), server-owned constraints; no JSON enforcement.
- [x] 2.4 Structured output / JSON: DeepSeek JSON mode, centralized strict schema/parser, validated answer object, safe failures and OpenAPI.
- [x] 2.5 Few-shot examples: two server-owned synthetic demonstrations, schema-valid assistant JSON, distinct failure modes and final-user ordering tests.
- [x] 2.6 Hallucination / uncertainty handling: six synthetic cases, real baseline/final runs, recorded semantic review and metrics; general prompt correction for demonstration carryover.
- [x] 2.7 Prompt injection basic boundaries: six synthetic cases, real baseline/final evidence, general authority/confidentiality/format constraints; limited semantic review.
- [x] 2.8 Token / context efficiency: real component measurements and full baseline/candidate comparison; candidate rejected and production prefix unchanged.
- [x] 2.9 Prompt versioning: immutable analytics-v1, server-owned active registry, checksum validation, rollback procedure and log/eval identity.
- [x] 2.10 Prompt regression infrastructure: unified 15-case suite and evidence-bound review; current live result 14 pass/1 fail, release gate blocked. analytics-v1 unchanged.
- [x] 2.11 Phase 2 integration: 66 offline tests and real Docker /chat/log/OpenAPI verification passed; analytics-v1 remains 14/15, release gate blocked.
- [x] 2.12 Phase 2 final verification: analytics-v2 published at its accepted SHA; three unchanged gate-v2 rounds PASS, 87 offline tests pass, Docker health/database/chat/logging/OpenAPI/client boundaries verified. Historical v1 14/15 blocked remains preserved. See [final acceptance](phase2-acceptance.md).

Phase 2 remains accepted and released. Phase 3 completed first local acceptance through 3.11. See [design](phase3-rag-design.md) and [acceptance](phase3-final-acceptance.md). Phase 4.1–4.4 now provides internal Tool Calling foundations, one fixed-query PostgreSQL read-only tool, deterministic single-call orchestration and a disabled-by-default DeepSeek adapter. Arbitrary SQL, public tool access, Agent and LangGraph remain unstarted.

## Phase 3.7 — Grounded generation implemented

Independent `rag-grounded-v1` and `rag-response-v1`, current-context source-label checks, real local DeepSeek token counting plus output/safety reserves. Existing /chat, analytics-v2 and retrieval/context remain unchanged. 134 offline + 31 database tests passed; one synthetic live response has a correct core answer and a documented limitation-relevance concern. No endpoint integration or ANN. See [generation verification and Phase 3.8 recommendations](phase3-generation.md).

## Phase 3.8 — RAG evaluation baseline

Independent frozen 15-case suite and layered rubric, deterministic DB pipeline and six real synthetic runs. 141 offline + 31 DB tests pass. Live subset: no observed hard/core blockers, 91.67% descriptive quality, unnecessary limitations in 3/6 answers. No release claim, prompt/config tuning or endpoint integration. See [evaluation evidence and limitations](phase3-evaluation.md).


## Phase 3 local acceptance — 2026-10-15

Phase3.8–3.11 completed locally with rag-grounded-v5, preserved historical artifacts, independent gate PASS,174 offline/31 database tests and Docker real API smoke. Base RAG endpoint remains default-disabled; explicit loopback-only override enables it with empty authorization scope. No public release, repeated-run stability claim or commit/push. See [acceptance](phase3-final-acceptance.md).


### Phase 3 closeout — 2026-10-16

First local acceptance is complete, not repeated-run stability verification or public production certification. README now documents the disabled default, explicit local enablement, empty server-owned document allowlist and non-paid test commands. Preserve all historical prompt SHAs, frozen cases/rubrics and raw/reviewed evidence. No new paid regression or Phase 4 development in closeout.

## Phase 4 — Tool Calling

- [x] 4.1 Tool Calling foundation: strict provider-independent call/result schemas, server-owned registry and authorization, fail-closed executor, safe metadata logging, and deterministic synthetic `get_sales_summary`. No public API, LLM selection, SQL or arbitrary code execution. See [design](phase4-tool-calling.md).
- [x] 4.2 PostgreSQL read-only data tool: versioned table migration, idempotent synthetic seed, fixed parameterized query, verified read-only transaction, source metadata and database security/error tests. The Phase 4.1 in-memory tool remains available. See [design](phase4-postgres-tools.md).
- [x] 4.3 Deterministic orchestration and provider-independent tool-call parsing: strict decision/result contracts, authorized schema exposure, one-call/two-turn bounds, untrusted tool evidence, sanitized logs and offline Fake Provider coverage. See [design](phase4-orchestration.md).
- [x] 4.4 DeepSeek Tool Calling local integration: official `tool_calls`/`tool_call_id` adapter, disabled internal feature flag, one-call/two-request bound, no retry, safe token/latency/cost telemetry, Fake/Mock/error coverage and one synthetic live smoke test. Repeated-run stability and a release gate remain Phase 4.5 work. See [design](phase4-deepseek-tool-calling.md).
- [x] 4.5a Tool Calling evaluation foundation: frozen 16-case synthetic suite, evidence-bound review, strict telemetry contract, Fake/Mock/PostgreSQL coverage and preregistered release gate. Seven deterministic controls pass. See [evaluation design](phase4-tool-calling-evaluation.md).
- [x] 4.5b Versioned evaluator correction and source exposure: v1/v2/v3 artifacts preserve their own suites, policies, SHAs, raw evidence and verdicts. Evaluator v3 separates authorization/source policy and validates business metrics without confusing period/version/telemetry values. Production and in-memory evaluation tool catalogs are server-bound. See [Evaluator v3](phase4-tool-calling-evaluator-v3.md) and [exposure policy](phase4-tool-exposure-policy.md).
- [x] 4.5c Frozen v3 live evaluation history: the first three rounds remain FAIL because local Docker/PostgreSQL was unavailable for six tool executions. Infrastructure diagnostics and the original evidence remain preserved. See [historical acceptance](phase4-tool-calling-live-v3-acceptance.md) and [diagnostics](phase4-tool-calling-v3-infrastructure-diagnostics.md).
- [x] 4.5d Final guarded recovery: unchanged frozen v3 Suite/Evaluator/Gate/Prompt/config completed three new independent 9-case rounds at 27/27 PASS; 12/12 database guards were healthy, Gate PASS, zero retries, cost USD 0.003135582. Final verification: 285 offline and 44 database tests. See [acceptance](phase4-tool-calling-live-v3-recovery-acceptance.md).

Phase 4 is complete for local engineering acceptance. Tool Calling remains default-off with no public API. Production readiness is deferred: identity/RBAC, least-privilege production database access, durable audit/usage storage, secrets management, service availability/SLOs, deployment controls and human approval for consequential tools are not implemented. Phase 5 has not started.
