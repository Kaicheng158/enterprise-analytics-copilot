# Phase 5.7a internal Agent API foundation

## Purpose

This phase provides the smallest controlled interaction surface above the accepted Planner and Durable Runtime. It creates task submission and status-query contracts without publishing an Agent product endpoint or adding another execution architecture.

The separate entrypoint is `backend.agent_app:app`. It is absent from the existing chat and RAG applications and is not referenced by Compose. `INTERNAL_AGENT_API_ENABLED=false` by default, and a real runtime controller is not constructed automatically.

## Request and authorization boundary

`POST /internal/agent/tasks` requires:

- `task=compare_quarterly_sales`;
- explicit baseline and comparison periods;
- an allowed region (`all`, `north`, or `south`);
- a constrained 8–200 character `Idempotency-Key` header;
- a matching server-configured `X-Internal-Agent-Key` of at least 32 characters.

The body uses strict Pydantic validation and rejects client-supplied provider/model, prompt, Tool list, SQL, principal, scope or execution settings. The shared key is compared in constant time and is never logged or returned. Authorization creates a fixed server-owned caller: principal `analytics-copilot-internal`, scope `internal-agent:analytics-copilot`.

The shared key is a local foundation control, not production identity. Production work must replace it with workload identity, scoped RBAC, rotation and auditable authorization decisions.

## Durable reuse and status contract

`DurableAgentTaskController` adapts the existing `DurableAgentRuntime` and `PostgresCheckpointStore`:

1. submission converts the strict API request into the existing `PlanningRequest`;
2. the existing Planner schema, request binding, `PlanValidator`, Tool authorization and execution budgets remain authoritative;
3. the existing HMAC idempotency path returns the same execution for a repeated key/request;
4. status lookup checks the persisted execution belongs to the fixed scope;
5. execution/checkpoint ID, plan identity, state version and status must agree before a response is produced;
6. a completed final value is parsed through the existing `SalesGrowthComparison` schema.

The standardized `internal-agent-task-v1` response includes task ID, durable status, whether submission created a task, the accepted bounded request, completed/total steps, validated final result or safe terminal error, state version, policy/runtime versions and update time. It does not return raw prompts, model responses, credentials, idempotency hashes, leases, private reconciliation evidence or arbitrary Checkpoint bodies.

`GET /internal/agent/tasks/{task_id}` returns `queued`, `running`, `needs_reconciliation`, `completed` or `failed`. Cross-scope and unknown identifiers both return the same safe not-found response. A version change between execution and Checkpoint reads returns `agent_status_changed` so the client can retry rather than receive a mixed snapshot.

## Deliberate exclusions

There is no HTTP endpoint to run, resume, reconcile, cancel or mutate a task. There is no public deployment profile, production Worker supervisor, real identity/RBAC, rate limit, quota/billing, human approval flow or event stream. The default Agent app has an unavailable controller even if someone enables only its HTTP flag.

The current request supports the sales-comparison Plan already accepted in Phase 5. RAG is not duplicated or silently inserted. A future evidence-backed Agent task should invoke the existing scoped `RagService`, Context Builder, grounded prompt and citation validator as a bounded service step.

## Verification

Offline tests cover default-off settings, key strength, missing/incorrect authorization, strict request and idempotency validation, rejection of client control fields, fixed caller/scope, standardized OpenAPI/response, normalized errors without secret leakage, completed-result validation, cross-scope denial, idempotency conflict and concurrent status-snapshot detection.

The isolated PostgreSQL test uses a Fake Planner and the existing real Tool Registry/Executor, Durable Worker and Checkpoint Store. It submits a task, observes `queued`, executes the two read-only PostgreSQL sales calls and local comparison, observes `completed` with 50.00% growth, verifies three successful Receipts and confirms a duplicate submission returns the same task without another Planner call. The test creates and drops a unique database; it does not migrate the existing application database or call a paid API.

Final local verification passed 391 offline tests and 91 isolated database tests. A review of the earlier intermittent Heartbeat failure found a test synchronization defect: a fixed 130 ms delay was being used to infer two successful 50 ms database renewals, but scheduler and database round-trip time made that inference nondeterministic under concurrent suite load. The test now waits on successful real `renew_lease` completions and keeps the original requirement of at least two renewals per Tool step and four overall. Concurrent stress repetitions pass without weakening the assertion. Existing Lease-owner, expiry, CAS-conflict, stale-worker-write, takeover and unknown-result reconciliation tests also pass, so this change does not alter or bypass runtime fencing.

## Next steps

Phase 5.7b should add an approved local composition profile and supervised Worker lifecycle while retaining all default-off boundaries. It should define shutdown, cancellation, queue/backpressure and operator reconciliation behavior before any wider exposure. Phase 5.7c can then add a bounded RAG-backed task type by reusing the existing RAG pipeline and security/evaluation artifacts.
