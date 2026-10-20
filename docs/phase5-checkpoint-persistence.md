# Phase 5.3 — Checkpoint Persistence and Crash Recovery

## Scope and isolation

Phase 5.3 implements a durable planning prototype around the existing deterministic Phase 5 plan. It is intentionally isolated from production schema and runtime:

- the SQL fixture exists only at `tests/db/fixtures/planning_checkpoint_v1.sql`;
- every database test creates and drops a uniquely named disposable PostgreSQL database;
- no file was added to `migrations/`, and the application startup path never creates these tables;
- no HTTP route, live LLM planner or paid provider call is added;
- Phase 4 orchestration, `/chat`, `/rag/chat`, frozen Prompt releases and historical evidence are unchanged.

The test schema revokes `PUBLIC` access and contains three relations: `executions`, `checkpoints`, and `step_receipts`.

## Durable contracts

### Execution

An execution binds a server-generated UUID to an authenticated scope, keyed idempotency hash, request SHA-256, immutable plan JSON/SHA/version, policy/runtime versions, durable status, monotonic state version, checkpoint sequence and short worker lease. The raw idempotency key is never stored.

Reusing the same scoped key with the exact request and plan returns the existing execution. Reusing it with a different request or plan fails with `idempotency_key_conflict`.

### Checkpoint

Each transition appends an immutable `AgentState` snapshot with its execution status, sequence, state version, SHA-256 and timestamp. The execution row and checkpoint are written in one transaction. A write requires:

- ownership of a non-expired lease;
- the caller's expected state version to match both execution and latest checkpoint;
- a legal Phase 5.2 state transition;
- a consistent execution/agent status.

The update uses compare-and-swap. A stale worker receives `checkpoint_cas_conflict`; its proposed counters and output are discarded.

### Step receipt

A receipt is unique by both `(execution_id, step_id)` and `(execution_id, idempotency_token)`. It binds action, input hash, status, optional protected output, output hash, tool call ID, timestamps and sanitized error code. The step token is an HMAC over execution ID, plan SHA and step ID.

Creating a started receipt and the corresponding running checkpoint is atomic. Completing the receipt and completed checkpoint is also atomic. A matching successful receipt deduplicates the step. Conflicting action, input or token is rejected.

## Lease and recovery behavior

A worker can claim only a pending/running execution with no lease, its own lease or an expired lease. Lease TTL is bounded to 1–300 seconds. Another worker cannot run while the current lease is valid.

The simulator deliberately exposes three crash points:

1. `after_start`: the started receipt and running checkpoint exist, but the operation was not observed;
2. `after_operation`: the external/local operation returned, but no success receipt was committed;
3. `after_checkpoint`: the success receipt and checkpoint are durable, but the worker stopped before proceeding.

After lease expiry, case 3 resumes from the next dependency-ready step and does not execute the completed step again. Cases 1 and 2 are conservatively treated as unknown outcomes because persistence alone cannot prove whether an operation crossed its side-effect boundary. The receipt becomes `ambiguous`, the execution becomes `reconciling`, the lease is released, and automatic execution stops. There is no blind retry.

A production implementation would need an upstream idempotency receipt, transactional outbox or an authorized reconciliation/human-review path before resolving an ambiguous consequential action.

## Idempotency and consistency boundaries

Database uniqueness and checkpoint transactions prevent duplicate local receipts. They do not provide global exactly-once execution across a network boundary. Authorization must still be reapplied on every resumed step, and an expired lease never grants permission by itself.

The current prototype stores synthetic step output as JSON to make state/receipt consistency directly testable. Production design must use protected references or encryption, retention policy and tenant-aware authorization rather than ordinary business payloads in telemetry or broadly accessible tables.

## Verification

Coverage includes:

- scoped creation and idempotency conflict;
- raw key non-persistence;
- lease exclusion and expired-lease takeover;
- compare-and-swap conflict;
- successful receipt deduplication;
- full deterministic three-step completion;
- crash after durable completion and safe resume;
- interrupted/unknown operation entering reconciliation without retry;
- isolated schema permissions and absence from production migrations;
- strict model, timestamp, schema-name and HMAC-secret validation;
- fail-closed plan, checkpoint-state and successful-receipt content-hash verification.

Phase 5.3 acceptance uses only local Python and disposable PostgreSQL databases. The complete run passed 319 offline tests and 55 isolated database tests. The recovery demonstration completed all three steps exactly once after a post-checkpoint crash, and an uncertain post-operation crash stopped at `reconciling` with one operation call and an `outcome_unknown` receipt.

## Remaining risks and next step

This prototype has no production migration, encrypted checkpoint payloads, tenant identity/RBAC, lease heartbeat, cleanup/retention worker, cancellation, reconciliation UI, metrics/SLOs or multi-process load test. PostgreSQL availability and transaction boundaries are tested locally, not under distributed failure or failover.

Recommended Phase 5.4 work is to freeze an evaluation policy for planning correctness and recovery safety before considering any controlled live planner. The durable schema should remain test-only until identity, protected storage, operational reconciliation and migration/rollback design are reviewed.
