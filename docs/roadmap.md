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

Phase 4 is complete for local engineering acceptance. Tool Calling remains default-off with no public API. Production readiness is deferred: identity/RBAC, least-privilege production database access, durable audit/usage storage, secrets management, service availability/SLOs, deployment controls and human approval for consequential tools are not implemented.

## Phase 5 — Bounded Planning

- [x] 5.1 Bounded multi-step planning foundation: strict provider-independent `Plan`, `PlanStep` and `AgentState` contracts; ordered dependencies; server-owned tool/computation policy; step, tool-call, timeout and execution-budget limits; fail-fast state transitions; default-off runtime; and a deterministic PostgreSQL Q1/Q2 comparison with local Decimal growth calculation. Local acceptance passed 297 offline and 46 database tests without paid API calls. Existing Phase 4 orchestration and `/chat`/`/rag/chat` behavior remain unchanged. See [design](phase5-bounded-planning.md).
- [x] 5.2 Deterministic plan validation and recovery design: canonical plan identity, unknown dependency and cycle detection, declared-order and server action binding, guarded step/agent transitions, safe bounded-execution exception handling, and a persistence/recovery/idempotency design without a production migration. Acceptance passed 311 offline and 46 database tests using Fake Provider/local services only. See [design](phase5-plan-validation-and-recovery.md).
- [x] 5.3 Isolated checkpoint persistence and crash recovery: test-only PostgreSQL `executions`/`checkpoints`/`step_receipts`, scoped HMAC idempotency, short leases, state-version compare-and-swap, atomic receipt/checkpoint writes, completed-step replay prevention, and fail-closed reconciliation for ambiguous in-flight work. Acceptance passed 322 offline and 57 isolated database tests. No production migration, endpoint or paid provider. See [design](phase5-checkpoint-persistence.md).
- [x] 5.4a Controlled structured planner foundation: provider-independent single-call Plan generation contract, versioned server-owned prompt/schema, strict response boundary, independent default-off feature flags, and Fake transport coverage through existing validation/authorization/budget/state controls. Verification passed 330 offline and 59 isolated database tests. No live adapter or paid call. See [design](phase5-controlled-planner.md).
- [x] 5.4b Planner evaluation and DeepSeek adapter foundation: independent frozen ten-case suite/rubric/gate, ten passing deterministic controls, strict default-off JSON planner adapter, Mock transport/error coverage, call/cost safety stop and sanitized non-paid preflight. Verification passed 345 offline and 59 isolated database tests; preflight is READY with zero paid calls. The release gate remains BLOCKED pending three independent live rounds. See [design](phase5-controlled-planner.md).
- [x] 5.4c Bounded live Planner acceptance: unchanged frozen identities completed three rounds × three cases at 9/9 PASS, zero retries, 10,993 tokens, 14,299 ms provider latency and estimated cost USD 0.0057585 under the USD 0.02 cap. The final Gate is PASS; historical preregistration BLOCKED evidence remains intact. Final verification passed 352 offline and 59 isolated database tests. Planner flags returned to false and no public route was added. See [acceptance](phase5-planner-live-acceptance.md).
- [x] 5.5a Durable controlled Planner integration: generated Plans pass the existing schema/request/PlanValidator boundary before entering the Phase 5.3 execution/checkpoint/receipt store. Scoped idempotency avoids repeat generation for known submissions; existing Lease, CAS and recovery rules prevent replay, coordinate takeover and stop ambiguous outcomes in reconciliation. Fake/Mock only, disposable schema, default-off and no public API. Verification passed 357 offline and 64 isolated database tests. See [design](phase5-durable-planner.md).
- [x] 5.5b Durable persistence foundation: versioned private database migration, transactional Checkpoint/Receipt consistency, bounded Lease heartbeat and expiry takeover, state-version CAS, restart/duplicate recovery and fail-closed reconciliation records for unknown external outcomes. Verification passed 357 offline and 70 isolated database tests. Disposable database only, default-off, no public API and no paid call. See [design](phase5-persistence-foundation.md).
- [x] 5.5c Worker fencing and reconciliation: server-owned Lease sessions, safe heartbeat/expiry takeover, stale-worker write rejection, unknown-result stop, authorized evidence-bound success/failure resolution, Tool Registry output validation and atomic reconciliation audit events. Verification passed 361 offline and 75 isolated database tests. Default-off, disposable database only, no public API or paid call. See [design](phase5-worker-reconciliation.md).
- [x] 5.5d Timed-heartbeat Worker Runtime: real heartbeat scheduling during Fake/Mock operations, lease-loss safe stop, concurrent Worker exclusion, expiry takeover, stale-write fencing, crash/restart recovery, operation timeout and full Checkpoint/Receipt/Reconciliation integration. Verification passed 365 offline and 81 isolated database tests. Default-off, no public API, paid call or existing-database migration. See [design](phase5-durable-worker-runtime.md).
- [x] 5.5e Durable Agent end-to-end integration: Fake Planner → strict Plan validation → timed Worker Runtime → authorized PostgreSQL Tool Executor → transactional Checkpoint/Receipt → crash recovery/reconciliation → final result. Tests cover automatic heartbeat, concurrent takeover prevention, idempotent request/replay, post-checkpoint restart, stale Lease/CAS rejection, unknown-result reconciliation and invalid Plan rejection. Verification passed 368 offline and 87 isolated database tests. Default-off, disposable database only, no public API, paid call or existing-database migration. See [design](phase5-durable-agent-integration.md).
- [ ] 5.5f Human-in-the-loop and consequential-action approval boundaries.
- [x] 5.6 Controlled DeepSeek Planner and Durable Runtime integration: the real DeepSeek Planner adapter is exercised through Mock HTTP, then accepted Plans use the existing strict validator, authorization, execution budgets, PostgreSQL tools, timed Worker, Checkpoint/Receipt and recovery path. Tests cover authorization, step and provider-cost limits, exact results, duplicate requests, provider/error stop and restart without Plan regeneration or completed-step replay. Verification passed 372 offline and 89 isolated database tests. All flags default off; no public API, paid call or existing-database migration. See [integration report](phase5-final-integration.md).
- [x] 5.6b Durable live acceptance: the frozen three-case suite completed two independent rounds at 6/6 PASS. Hard invariants were 100%, core blockers zero and normalized quality 100% in both rounds. Six DeepSeek calls used 7,364 tokens, 11,672 ms provider latency and estimated USD 0.0038814 with zero retries under the USD 0.02 cap. The final Gate is PASS; all flags returned to false and no public API was added. Original evidence and the explicitly labelled zero-paid trace replay remain separate. See [plan](phase5-durable-live-acceptance-plan.md) and [acceptance](phase5-durable-live-acceptance.md).
- [x] 5.7a Internal Agent API foundation: separate default-off app, strict quarterly-sales request, server-owned internal principal/scope, constant-time shared-key check, idempotent submission, standardized task response and scope-safe status lookup. The durable adapter reuses the existing Planner/Worker/Tool/Checkpoint stack; Fake/Mock tests and one disposable PostgreSQL test cover validation, authorization, safe errors, duplicate submission, status transition and final result. No public deployment, paid call or existing-database migration. See [design](phase5-agent-api-foundation.md).
- [x] 5.7b Protected local HTTP integration: an independent default-off localhost app composes the existing Fake Planner, validation, durable Worker, PostgreSQL Tool and Checkpoint/Receipt store in a disposable database. Tests cover authorization failure, loopback enforcement, cross-scope hiding, status/result reads, concurrent idempotent submission, request conflict and safe provider errors. Verification passed 395 offline and 96 isolated database tests plus a real localhost Uvicorn smoke with successful disposable-database cleanup. This is a synchronous local harness; durable queueing, cancellation, production Worker supervision and workload identity/RBAC remain future work. See [design](phase5-local-agent-http.md).
- [x] 5.7c Persistent task queue and independent Worker foundation: existing execution rows form a scoped PostgreSQL queue; atomic `FOR UPDATE SKIP LOCKED` claiming reuses Lease/Heartbeat/CAS/Checkpoint/Receipt and recovery. HTTP submission returns queued and GET reads real durable state. Verification passed 399 offline and 103 isolated database tests covering polling, concurrent claims, duplicates, crashes, expiry takeover, timeout and fail-closed reconciliation. No migration, dependency, public deployment or paid call. See [design](phase5-persistent-agent-queue.md).
- [x] 5.7d Bounded Worker lifecycle foundation: a default-off supervisor hosts a fixed number of existing persistent queue Workers, stops new claims before graceful drain, reports shutdown timeouts, applies atomic per-scope PostgreSQL admission/backpressure and exposes body-free lifecycle/queue counters. Tests verify no lost queued work, restart recovery, concurrent claims, Lease/CAS safety and fail-closed unknown effects. Verification passed 404 offline and 106 isolated database tests. Production process management, cancellation, workload identity/RBAC, exported metrics, orphan cleanup and operator reconciliation remain future work. See [design](phase5-worker-supervisor.md).
- [x] 5.7e Local sales Agent product slice: a default-off, loopback-only page submits the existing strict quarterly-sales task through Agent API authentication, the real DeepSeek structured Planner protocol, durable PostgreSQL queue and independent Worker, then polls authoritative status and renders a source-backed result. Verification passed 409 offline and 108 isolated database tests using Mock HTTP/disposable PostgreSQL only; no paid call or public deployment. RAG remains unchanged and is not needed for this database task. See [design](phase5-local-agent-product.md).
- [ ] 5.7f Production Worker/API operations: deploy separate managed processes and add cancellation semantics, workload identity/RBAC, exported metrics/alerts, persistent non-disposable tasks, orphan cleanup and authorized operator reconciliation.
- [ ] 5.8 Agent + RAG task type: reuse the existing `RagService`, retrieval security boundary and citation contract as a bounded evidence step; do not duplicate ingestion, retrieval, prompts or generation.

## Independent Frontend / UI MVP track

- [x] Phase 5.8a frontend refinement: Workspace v4.1 Login/Register, seven-entry bilingual shell, responsive layouts, token-aligned components and honest Demo boundaries are locally accepted. Final auth detail checks cover EN/中文 at 1440/768/390, keyboard password visibility, one Demo entry and no credential read/send/store. Figma native Auth synchronization and exact native-frame comparison remain pending and are not reported complete. See [visual review](phase5-8a-workspace-visual-qa.md), [alignment](phase5-8a-final-figma-alignment.md) and [auth acceptance](phase5-8a-auth-refinement-acceptance.md).
- [x] Phase 5.8b Safe Workspace Fake/Mock Agent integration: the default-off same-origin Bridge reuses the existing strict Agent request, authorization, idempotency, queue/Worker, Checkpoint/Receipt and PostgreSQL sales Tool. The Workspace supports only the fixed synthetic quarterly comparison, bounded monotonic polling, same-key lost-response recovery, truthful durable states and verified result/source rendering. The Bridge enforces loopback peer/Host, exact same-origin write Origin, Fetch Metadata and self-only CSP while keeping the internal Agent key server-side. Verification passed 414 offline tests, 110 isolated database tests, 14 frontend unit tests, browser/i18n/responsive/accessibility suites and a real localhost Bridge smoke with disposable-database cleanup. No paid model, public endpoint, arbitrary prompt/SQL/tool/model input, real auth, upload or full report is implied. See [design and evidence](phase5-8b-workspace-agent-integration.md).

**Current UI baseline (2026-10-25): Workspace v4.1 bilingual HTML + handoff.** The sales-only four-view navigation below is historical. The independent `frontend/workspace/` now provides the seven-entry workspace shell, demo Login/Register, Settings language controls and keyed EN/zh-CN resources without changing the existing console/API. See [v4.1 audit, implementation and handoff](workspace-v41-frontend.md). UI, answer and report languages are independent; no backend integration or identity feature is implied. Next: Figma v4.1 components/responsive acceptance, then separately scoped safe Fake Agent API integration (idempotency/polling/recovery first).

This is a design/product workstream, separate from Phase 5 backend milestones. No runtime, API, database, flags or frozen evaluation changes are part of design preparation.

- [x] UI-MVP-0: integrate reviewed v1.1 brief; specify four buildable views, responsive layouts, tokens/components, API mapping and required idempotency/polling/recovery fixes. Documentation prepared; Figma progress is tracked separately below.
- [x] UI-MVP-D1a: Figma file created; first drafts of four desktop core views completed, as confirmed by the user. This is progress confirmation, not a design acceptance verdict.
- [ ] UI-MVP-D1b: Design System/tokens/reusable components → 390px mobile layouts → exceptional states → clickable Prototype.
- [ ] UI-MVP-D2: independent visual, responsive, interaction, accessibility and API-boundary review; user confirmation before implementation.
- [ ] UI-MVP-1: incremental frontend implementation against existing Agent API, with Fake/Mock behavioral tests and default-off flags.
- [ ] UI-MVP-2: local browser and isolated database verification; no paid model call.
- [ ] Separately authorized live product smoke, with preregistered budget.

Dashboard, AI Chat/RAG integration, Task History and Account/Workspace are **Future Concepts**, not available Sales Agent features. See [workflow](ui-mvp-workflow.md), [Figma handoff](ui-mvp-figma-handoff.md) and [reviewed brief](mvp-figma-design-brief.md).

Design file: [Enterprise Analytics Copilot — Figma MVP](https://www.figma.com/design/GxZ8cOgDc4Wj7XUJN1aAJO). Desktop draft progress is user-confirmed; no Figma node inspection or design acceptance was performed during this documentation sync. Existing API contracts, Fake/Mock integration, default-off flags and security boundaries remain unchanged.

## Product Vision v2 规划索引 — 2026-10-28

新的产品规划入口为 [Product Vision v2](product-vision-v2.md)、[Product Roadmap v2](product-roadmap-v2.md) 与 [数据生命周期及项目协作架构初稿](project-data-architecture-v2.md)。本文件继续保留历史阶段、验收结果与原有编号；v2 规划不会回写或覆盖冻结证据。

Product Vision v2 将主链路定义为上传文件→验证→分析与对话→可视化→简答或完整报告，并将 Sales Comparison 保留为已实现模板。新增功能必须先通过 v2 Roadmap 中的 Feature Value Gate v1，证明减少用户工作量并服务 Investigate、Simulate、Decide & Review 或 Collaborate 主线。
