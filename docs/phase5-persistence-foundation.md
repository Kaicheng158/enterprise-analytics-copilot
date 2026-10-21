# Phase 5.5b — Durable persistence foundation

## Scope

Phase 5.5b promotes the Phase 5.3 checkpoint tables from a test fixture to the versioned `005_planning_checkpoints.sql` migration. The migration creates the private `agent_runtime` schema with `executions`, append-only `checkpoints`, and per-step `step_receipts`, plus status/lease lookup indexes and public-access revocation. Verification applies the migration only to unique disposable databases; it was not applied to the existing local application database or any production database.

The runtime remains default-off through `DURABLE_LLM_PLANNER_ENABLED=false`. No route or paid provider integration was added.

## Consistency and concurrency rules

- Execution `state_version` and `checkpoint_sequence` advance together under row lock and compare-and-swap.
- A successful step result and its completed checkpoint are committed in one database transaction.
- `audit_execution_consistency` verifies execution/latest-checkpoint identity, status/version alignment, Plan step membership, completed output/receipt agreement, ambiguous-state alignment, and terminal completeness.
- A lease heartbeat requires the same owner, a non-expired lease, the expected state version, a runnable status, and a bounded TTL. It does not advance the state version.
- Competing workers cannot own the same active lease. After expiry, one worker may take over; stale state versions still fail CAS.
- Scoped HMAC idempotency prevents a known request from creating another execution after process restart. A concurrent first submission can still duplicate upstream planning cost before one database insert wins.

## Crash recovery and reconciliation

A committed completed receipt is authoritative after restart and prevents that step from running again. A crash after the external operation but before the database commit is different: the external result cannot be inferred from a local uniqueness constraint. Recovery marks the receipt `ambiguous`, changes the execution to `reconciling`, and returns a `ReconciliationCase` with `retry_allowed=false` and `required_action=verify_external_outcome`.

This is deliberately **not an exactly-once guarantee for external tools**. Database uniqueness provides one durable receipt identity and blocks local replay after known completion. Exactly-once external effects require a provider-supported idempotency key or a domain reconciliation query. Until external outcome verification exists, ambiguous work remains stopped and must not be retried automatically.

## Local verification

Tests cover migration history and permissions, concurrent lease acquisition, heartbeat renewal, expired takeover, stale heartbeat CAS, cross-process request deduplication, completed Checkpoint/Receipt consistency, crash/restart recovery, and a simulated non-idempotent external effect whose unknown result remains stopped for reconciliation.

Complete local verification passed 357 offline and 70 isolated database tests.

Remaining production work includes database-role grants, operational migration rollout/rollback procedure, heartbeat scheduling around long-running tools, verified domain-specific reconciliation handlers, observability/alerts, retention, cancellation, human approval, and distributed load/chaos testing.
