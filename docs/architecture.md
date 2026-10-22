# Architecture

Browser -> 127.0.0.1:8765 -> API container port 8000 (Uvicorn/FastAPI) -> postgres:5432 (PostgreSQL container).

- GET /health checks API responsiveness.
- GET /health/db executes SELECT 1 using Psycopg, with bounded connection/query timeouts.
- Compose starts the API after PostgreSQL passes its healthcheck; API healthcheck queries /health/db.
- PostgreSQL data persists in the existing named volume.
- Credentials are injected at runtime from the ignored local .env; no secrets are copied into the image.
- API runs as a non-root user. Only localhost ports are published.

Phase 1 chat flow: POST /chat -> validated ChatRequest -> LLMProvider protocol -> DeepSeekProvider -> official DeepSeek Chat Completions API -> normalized ChatResult.

Provider selection is wired by get_provider; routes do not construct provider-specific HTTP requests. Only DeepSeek is supported today. The API key is injected at runtime; health endpoints do not require a configured LLM key.

Future direction (not implemented): provider/model switching and quality/latency/cost comparisons; platform-managed keys or BYOK; usage persistence, user quotas and billing. A future key resolver belongs at provider construction, rather than the business route. Current usage counters are provider-reported counts; versioned price configuration provides estimates, not billed charges.

RAG is implemented behind its separate local release boundary. Internal Tool Calling now includes a server-owned registry, validated executor, fixed read-only PostgreSQL tool, bounded orchestration, source-exposure policy and a disabled-by-default DeepSeek Tool Calls adapter. It is not connected to a public Tool API. Agent autonomy and LangGraph are not implemented.

## Phase 4 Tool Calling boundary

Internal trusted caller -> server-owned exposure profile -> Tool Registry -> Tool Executor -> registered deterministic handler.

For DeepSeek-assisted selection, the bounded flow is:

1. expose at most the tools allowed by the server-owned profile;
2. request one strict tool decision from DeepSeek;
3. validate tool name and arguments, then authorize independently in the registry/executor;
4. execute at most one registered tool;
5. wrap the result as untrusted evidence;
6. permit one final model response and stop.

The runtime allows no arbitrary SQL, code execution, dynamic tool registration or model-selected exposure profile. The production profile exposes only `get_postgres_sales_summary`; the evaluation fixture profile exposes only `get_sales_summary`. The PostgreSQL tool uses a fixed parameterized query, an explicit read-only transaction and exact monetary arithmetic. Tool logs contain correlation and status metadata without prompt bodies, database credentials or API keys.

Phase 4.5 evaluation assets are versioned separately. Historical v1/v2/v3 failures and raw evidence remain immutable. The final guarded recovery reused the frozen v3 Suite, Evaluator, Gate, Prompt, source binding and DeepSeek config. It passed three independent nine-case rounds. A separate database runtime guard checks `SELECT 1`, the sales relation, expected synthetic dataset and the real PostgreSQL tool before each PostgreSQL-dependent paid interaction; failure stops the remaining live run before another provider request.

Tool Calling remains an internal, default-off local capability. There is no HTTP Tool endpoint, user authentication/RBAC, production least-privilege database role, durable audit ledger, quota/billing enforcement, secret-manager integration, distributed availability control, consequential-action approval flow or production deployment. The current gate covers synthetic read-only analytics behavior; it does not certify production security, reliability or business correctness.

The provider emits correlated attempt and request-summary logs. A bounded HTTP retry loop surrounds each call. Request latency includes backoff; successful-attempt token usage and cost remain separate from unknown failed-attempt charges.

Central configuration: backend/config.py loads validated settings and the provider/price catalog. The provider factory selects the sole registered DeepSeek adapter. ChatRequest separates system/user content; the adapter serializes them as distinct role messages.

Phase 2.1–2.2 supersedes the Phase 1 system-message interface: public requests carry only user content; backend/prompts.py assembles the fixed system instruction and user message.

## Phase 5.1 bounded planning boundary

The internal `backend.planning` package adds a default-off planning layer above the existing Tool Executor. A provider may only propose a strict plan; it cannot register tools, grant authorization or execute actions. The server validates ordered dependencies, task scope and fixed execution bounds before sequential execution. The first plan calls the existing PostgreSQL sales summary tool twice and computes the quarter-over-quarter change locally with exact decimal arithmetic. Every transition produces a validated in-memory `AgentState` snapshot and any error stops remaining work.

No planning endpoint is registered, and `/chat`, `/rag/chat`, analytics-v2, RAG releases and frozen evaluation artifacts remain outside this boundary. See [Phase 5.1 design](phase5-bounded-planning.md).

### Phase 5.3 durable test boundary

The `backend.planning.checkpoint_*` modules add a PostgreSQL repository and deterministic crash simulator behind the existing internal planning boundary. A disposable test-only schema persists execution identity, immutable checkpoints and step receipts. Short leases coordinate workers; state-version compare-and-swap rejects stale transitions; scoped HMAC identities deduplicate requests and steps. A durable success resumes at the next dependency-ready step. Any started operation whose outcome cannot be proven moves to `reconciling` and stops. No production migration, public route or planner runtime imports this store. See [Phase 5.3 design](phase5-checkpoint-persistence.md).

### Phase 5.4a controlled planner boundary

`StructuredPlanProvider` converts a strict `PlanningRequest` into a server-owned prompt plus the strict Plan JSON schema and accepts at most one provider response. The model-facing layer cannot execute tools or supply authorization, limits, checkpoints or state. Returned JSON enters the existing local parser, request binding, `PlanValidator`, Tool Registry/Executor, budget enforcement and state transition guards. Two independent feature flags default to off, and no HTTP route or live network transport is present. See [controlled planner design](phase5-controlled-planner.md).

### Phase 5.4b DeepSeek planner and evaluation boundary

`DeepSeekStructuredPlannerTransport` is an internal adapter for the official Chat Completions JSON-output protocol. It is constructed only when the server-owned planner flag is enabled and a local key exists. Each transport accepts one request, performs no automatic retry, requires the frozen model/thinking/output configuration, validates usage and finish state, estimates peak-rate cost, and shares a guard capped at nine calls and USD 0.02. It cannot execute tools. Its JSON Plan still crosses the Phase 5.4a schema/request binding and all existing local validation, authorization, budget and state boundaries.

The separate frozen `planner-suite-v1` evaluates legal plans, dependency/cycle rejection, tool authorization, arguments, execution budget, injected control fields/instruction-like text and task completion. Applicable hard invariants require 100%; core blockers must be zero; the quality threshold is a preregistered provisional 85%. Ten Fake controls pass. Three independent live rounds subsequently passed the unchanged gate at 9/9 cases. A non-paid preflight verifies database/tool health, frozen identities, disabled feature flags, absent public routes, cost reserve and write-once evidence paths without exposing secrets. The capability remains internal and default-off after acceptance.

### Phase 5.5a durable controlled Planner boundary

`DurableControlledPlanner` is a thin composition over the Phase 5.4 provider/validator and Phase 5.3 persistence/recovery modules. Submission first checks the scoped HMAC idempotency identity; an existing request returns its durable execution without asking the provider to generate another Plan. A new Plan must pass strict parsing, request-scope binding and the existing `PlanValidator` before the existing store writes its execution and initial checkpoint.

Execution remains one durable step per lease. The existing recovery simulator creates a receipt before invoking the authorized tool/local calculation, commits the completed state through state-version CAS, and resumes only a dependency-ready step. A completed receipt prevents replay, an active lease rejects concurrent ownership, an expired lease permits takeover, and a started operation without a durable outcome moves to `reconciling`. No new persistence tables, production migration or route were added. The integration is enabled only by the separate default-false `DURABLE_LLM_PLANNER_ENABLED` setting and currently runs only with Fake/Mock providers in disposable test databases.


### Phase 5.5b production persistence boundary

The versioned `005_planning_checkpoints.sql` migration defines the private `agent_runtime` schema for durable executions, checkpoints and receipts. Store writes keep receipt and checkpoint transitions transactional, compare state versions under row lock, and support bounded lease heartbeats plus expiry takeover. A consistency audit verifies the aggregate across the three tables. Unknown external outcomes produce a non-retryable reconciliation record; database uniqueness alone is not treated as proof of exactly-once external effects. The migration is tested only in disposable databases and the runtime remains default-off with no public route.


### Phase 5.5c worker fencing and reconciliation

`WorkerLeaseCoordinator` binds a worker to a lease owner, expiry and state version. Heartbeats renew only a current lease; expired owners and stale versions cannot write. A takeover that finds an in-flight Receipt moves the execution to `reconciling` without repeating the operation. `ReconciliationService` requires a server-authorized actor, an evidence SHA-256 and the current CAS version. Confirmed tool output is revalidated through the registered Tool output model, and the resolution, Receipt, Checkpoint, Execution and reconciliation event commit atomically. This is a safe recovery protocol, not an exactly-once claim for external systems.


### Phase 5.5d timed-heartbeat Worker Runtime

`DurableWorkerRuntime` composes the existing recovery, Receipt, Checkpoint, Lease and reconciliation modules. A server-owned operation runs while the runtime periodically renews a Lease Session tied to the post-start state version. Completion performs a fresh lease check and then relies on transactional CAS. Heartbeat failure, timeout or takeover prevents the old Worker from persisting; an in-flight Receipt becomes an unknown result for evidence-bound reconciliation. Post-Checkpoint crashes resume at the next step without replay. The runtime is internal and default-off.


### Phase 5.5e durable-agent integration boundary

`DurableAgentRuntime` is a thin internal composition over `DurableControlledPlanner` and `DurableWorkerRuntime`. Submission still passes provider output through strict Plan parsing, request binding and `PlanValidator` before persistence. Execution still delegates every tool call to the authorized Tool Executor and every durable transition to the existing PostgreSQL store. The composition adds only a bounded run-to-terminal loop and a typed final result; it does not add alternate authorization, persistence or recovery behavior.

The tested chain is Fake Planner → Plan Validator → timed Worker → PostgreSQL sales Tool Executor → transactional Receipt/Checkpoint → optional evidence-bound reconciliation → final Decimal sales comparison. A live heartbeat prevents concurrent takeover. A committed Receipt prevents replay after restart. A lost lease or unknown external result stops progress until reconciliation, and stale workers cannot write through the Lease/CAS fence. Reconciliation validates the persisted JSON Tool Result through the same registered output schema and authorization policy.

The runtime requires the separate default-false `DURABLE_AGENT_RUNTIME_ENABLED` flag. It has no HTTP route, uses no paid provider, and was tested only against disposable databases. See [Phase 5.5e design](phase5-durable-agent-integration.md).


### Phase 5.6 controlled DeepSeek-to-durable composition

`build_durable_deepseek_agent_runtime` connects an existing `PlannerTransport` to `StructuredPlanProvider`, `DurableControlledPlanner`, the server-owned Tool Registry/Executor, `DurableWorkerRuntime` and `DurableAgentRuntime`. It adds no alternate schema, validator, authorization rule, execution budget, database writer or recovery protocol. The DeepSeek integration test supplies the actual `DeepSeekStructuredPlannerTransport` with a Mock HTTP opener, so request serialization, JSON-output mode, fixed model configuration, usage/cost validation and zero-retry behavior run without a paid network call.

An accepted Plan remains data until strict Plan parsing, request binding and `PlanValidator` succeed. A rejected or over-limit Plan is never persisted and cannot reach a Tool. A repeated scoped request returns the stored execution before another provider call. Restart recovery reads the persisted Plan and Checkpoint, so it does not regenerate the Plan and a successful Receipt is not replayed. All existing Planner, durable Planner, Worker and Agent flags must be enabled by trusted server composition; they remain false by default and no HTTP route invokes this builder.

### Phase 5.6b durable live-acceptance boundary

The live runner is an evaluation-only composition that requires both a dedicated process environment guard and an exact server-owned confirmation value. It creates a unique disposable PostgreSQL database for each round, applies the existing versioned migrations and synthetic sales seed, and checks `SELECT 1`, the expected eight rows, the private Agent schema and an authorized PostgreSQL sales Tool call before every provider request. The runner cannot change the frozen Planner prompt/schema, case set, gate, model settings or execution limits.

Each case allows one DeepSeek request and zero retries. The resulting Plan still passes request binding, `PlanValidator`, Tool Registry authorization and the durable Worker/Lease/CAS/Receipt path. Repeating the same scoped idempotency key must return the existing execution without a second provider call. Evidence is synthetic, secret-checked and write-once. A database failure, frozen identity drift, missing cost evidence, security failure or budget stop ends the run before another paid request. This boundary is not reachable from `/chat`, `/rag/chat` or any Agent endpoint, and all related feature flags remain false outside the explicitly authorized runner.

The frozen Phase 5.6b run subsequently passed both required rounds at 6/6 cases, with all hard invariants and quality rules satisfied. This is a local synthetic acceptance result, not authorization to expose the durable Agent publicly.

### Phase 5.7a internal Agent API boundary

`backend.agent_app:app` is an independent FastAPI entrypoint. It is not imported by `backend.main:app`, `backend.rag_app:app` or Compose. `INTERNAL_AGENT_API_ENABLED` defaults to false; an enabled app additionally requires a server-held key of at least 32 characters. Successful authorization creates the fixed `analytics-copilot-internal` principal and `internal-agent:analytics-copilot` scope. Neither value can come from the request.

`POST /internal/agent/tasks` accepts only the existing `compare_quarterly_sales` task with explicit baseline period, comparison period and region plus a constrained `Idempotency-Key` header. Model/provider selection, prompt text, Tool catalogs, SQL, scope and execution controls are rejected as extra fields. `GET /internal/agent/tasks/{task_id}` returns only a task in the caller's server-owned scope. A cross-scope identifier is indistinguishable from a missing task.

`DurableAgentTaskController` calls the existing `DurableAgentRuntime.submit` and reads the existing `PostgresCheckpointStore`; it does not execute work in the HTTP process or add a second persistence model. Responses map durable state to `queued`, `running`, `needs_reconciliation`, `completed` or `failed`, include bounded progress and return the existing `SalesGrowthComparison` only after strict validation. Execution and Checkpoint IDs, versions and statuses must form one consistent snapshot; a concurrent change returns a safe retryable conflict instead of mixing versions.

There is intentionally no HTTP run/resume/reconcile/cancel endpoint. Workers and reconciliation remain server/operator responsibilities. The default app also has no real controller composition, so enabling only the HTTP flag still returns `agent_runtime_unavailable`. A future RAG-aware task must call the existing scoped `RagService` and preserve its untrusted-evidence/citation rules rather than implementing another retrieval path.

### Phase 5.7b localhost integration profile

`backend.local_agent_app:app` is a second independent entrypoint used only for local integration verification. It requires both the internal API flag and `INTERNAL_AGENT_LOCAL_SERVICE_ENABLED`; both default to false. It rejects non-loopback ASGI peers and does not trust forwarding headers. The profile creates a server-named disposable database, migrates and seeds only that database, and removes it on graceful shutdown.

`DisposableLocalAgentRuntime` constructs the existing Planner, Tool Registry/Executor, timed Worker, durable Agent and Checkpoint Store. `LocalExecutingAgentTaskController` adds only a single-flight local supervisor around submission: it serializes concurrent submissions, delegates persistence/idempotency to the existing controller, runs the existing runtime, then reads the authoritative persisted response. This local supervisor is deliberately synchronous and is not a production queue or Worker service. The normal API, RAG API and Compose configuration do not import it.

### Phase 5.7c persistent task queue

The existing `agent_runtime.executions` table is also the durable queue; no parallel job table or status model exists. `claim_next_execution` filters the server-owned scope, uses the existing runnable states and Lease expiry, locks one oldest candidate with PostgreSQL `FOR UPDATE SKIP LOCKED`, and writes the Lease in the same transaction. The existing status/Lease index supports this query.

`PersistentAgentQueueWorker` has no HTTP dependency. It claims a task, passes the same Lease owner to `DurableAgentRuntime`, and relies on the established timed Worker for Heartbeat, state-version CAS, Checkpoint/Receipt transactions and unknown-result reconciliation. Its polling loop is bounded by a server-side interval and an explicit stop event. Database polling errors do not authorize work or expose exception bodies.

`backend.queued_agent_app:app` is only a default-off localhost integration profile. Its controller submits and reads persisted tasks but never executes them. Tests run the Worker separately against the same disposable PostgreSQL database, proving that status changes come from the durable Worker rather than the request handler. Neither this app nor the Worker is included in the normal API/RAG applications or Compose.
