# Phase 5.5e Durable Agent integration

## Scope

Phase 5.5e validates one complete internal durable-agent chain without adding a public API or a live provider:

`Fake Planner → Plan schema/request binding/PlanValidator → Worker Runtime → Tool Executor → PostgreSQL Checkpoint and Receipt → crash recovery or reconciliation → final result`

`DurableAgentRuntime` only composes existing Phase 5 components. It does not replace Tool Registry authorization, Planner validation, execution budgets, Lease ownership, state-version CAS, idempotency, Receipt deduplication or reconciliation policy. Its bounded loop executes at most eight steps and returns a strict terminal/paused/reconciling result.

## Verified behavior

- The Fake Planner emits the deterministic two-quarter comparison Plan. The Plan is validated before an execution is persisted.
- Both sales steps use the existing authorized `get_postgres_sales_summary` Tool Executor path and the disposable database's idempotent synthetic dataset.
- The local compute step verifies period, region, currency, relation and dataset version before calculating change with `Decimal`.
- Timed heartbeats retain a live lease during long tool operations; another Worker cannot take over.
- Reusing the same scoped idempotency key returns the existing execution before another Planner call. Re-running a completed execution returns its durable result without invoking a tool again.
- A crash after a completed Checkpoint resumes from the next step and does not replay the completed sales query.
- A heartbeat/database failure stops the old Worker. After lease expiry, takeover marks the in-flight result ambiguous and performs no retry. An authorized evidence-bound reconciliation can confirm the captured result, after which execution resumes at the next step.
- A stale Worker cannot write after takeover because Lease ownership and state-version CAS are checked independently.
- Persisted Tool Results use the executor's JSON contract. Reconciliation parses that JSON through the same strict registered output model, preserving Decimal and source validation without weakening authorization or schema checks.

## Isolation and defaults

All integration database tests create a uniquely named PostgreSQL database, apply versioned migrations there, seed non-sensitive synthetic sales, and drop the database afterward. No migration is applied to the existing application database. `DURABLE_AGENT_RUNTIME_ENABLED`, `DURABLE_WORKER_RUNTIME_ENABLED` and `DURABLE_LLM_PLANNER_ENABLED` remain false by default. No Agent route exists and no paid API is called.

## Verification

Local verification on 2026-10-21 passed:

- 368 offline tests;
- 87 isolated database tests;
- the focused nine-test durable-agent contract/integration set.

The end-to-end expected result was Q1 USD 400.00 versus Q2 USD 600.00, absolute change USD 200.00 and growth 50.00%, with three successful durable Receipts.

## Remaining production gaps

This is local engineering validation, not production certification. The operation thread is in-process and daemonized; there is no external queue, Worker supervisor, distributed cancellation or graceful shutdown protocol. Reconciliation requires trusted evidence but no operator workflow or human approval UI exists. Production identity/RBAC, per-tenant isolation, secret management, observability/SLOs, retention controls and deployment migration procedures remain unimplemented. External side effects are not claimed exactly once: unknown outcomes still require authoritative reconciliation before progress.
