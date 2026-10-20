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
