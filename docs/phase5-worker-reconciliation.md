# Phase 5.5c — Worker fencing and reconciliation

## Runtime boundary

Phase 5.5c remains an internal, default-off capability. It adds no public route and makes no paid provider call. All tests use Fake/Mock operations and unique disposable PostgreSQL databases. The existing application database is not migrated.

`WorkerLeaseCoordinator` creates a server-owned `LeaseSession` containing execution ID, worker ID, state version and expiry. Heartbeats can extend only a currently owned, unexpired, runnable lease at the same state version. A heartbeat never advances durable state. Once the lease expires, another worker may claim it; the old session cannot heartbeat, assert ownership or commit a checkpoint. Existing checkpoint writes still verify owner, expiry and CAS inside the transaction.

If takeover finds a `started` receipt, it does not repeat the operation. It atomically marks the receipt `ambiguous`, advances the execution to `reconciling`, clears the lease and stops normal execution.

## Evidence-bound reconciliation

Migration `006_planning_reconciliation.sql` adds the private, append-only-by-service `agent_runtime.reconciliation_events` table. It stores only a decision, server actor identity and evidence SHA-256; it does not store raw external evidence.

A reconciliation can proceed only when:

- the execution is still `reconciling`, lease-free and at the expected state version;
- exactly one matching receipt remains `ambiguous/outcome_unknown`;
- the actor is in the server-owned reconciliation policy;
- the evidence digest is a valid SHA-256;
- a confirmed tool result passes the same Tool Registry authorization and strict output model used by normal execution;
- the resulting AgentState is a valid transition from the latest checkpoint.

The resolution, receipt update, new checkpoint, execution CAS and reconciliation event are committed in one transaction. Concurrent resolution attempts yield one winner. Confirmed success resumes at the next dependency-ready step without replaying the reconciled tool. Confirmed failure terminates the execution. A second or stale resolution fails closed.

## Guarantees and limits

The design prevents stale database writes and blind retries. It does not make an external side effect exactly-once. If a tool supports idempotency keys or status lookup, the reconciliation worker must use those capabilities to determine the outcome before supplying evidence. Without external verification, the execution must remain in `reconciling`.

Local verification passed 361 offline and 75 isolated database tests. Coverage includes heartbeat renewal, lease loss, expired takeover, stale-worker write rejection, crash/restart, unknown outcomes, authorized success/failure reconciliation, Tool Registry output validation, concurrent resolution, replay prevention and private migration permissions.

Remaining production work includes a real heartbeat scheduler around long-running tools, external-provider-specific outcome probes, stronger operator identity/RBAC and approval workflow, evidence retention policy, audit export, migration rollout/rollback operations, metrics/alerts and distributed chaos/load testing.
