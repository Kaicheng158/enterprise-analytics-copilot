# Phase 5.5a — Durable controlled Planner integration

## Scope

This stage connects the controlled structured Planner to the existing Phase 5.3 persistence prototype. It does not add a new checkpoint system, production migration, public endpoint or real provider call. `DURABLE_LLM_PLANNER_ENABLED` defaults to false.

```text
strict PlanningRequest
  -> scoped idempotency lookup
  -> StructuredPlanProvider (Fake/Mock in this stage)
  -> strict Plan parsing and request binding
  -> existing PlanValidator / server authorization and budgets
  -> existing PostgresCheckpointStore
  -> one existing CrashRecoverySimulator step per lease
  -> registered tool or existing local sales-growth calculation
  -> receipt + checkpoint committed with state-version CAS
```

## Reused controls

The integration directly reuses:

- Phase 5.4 `StructuredPlanProvider`, strict Plan schema and `PlanValidator`;
- Phase 5.3 `executions`, `checkpoints` and `step_receipts` contracts;
- scoped HMAC idempotency identities and content hashes;
- short worker leases and expiry-based takeover;
- state-version compare-and-swap;
- deterministic step idempotency tokens and receipt uniqueness;
- crash recovery and the fail-closed `reconciling` state;
- the existing Tool Executor contract and local Decimal sales-growth computation.

No model field can set the scope key, idempotency key, principal, lease owner, lease TTL, checkpoint version or recovery outcome.

## Submission idempotency

The store now supports an HMAC-based lookup before provider generation. Retrying the same scope, idempotency key and request returns the existing Plan/execution/checkpoint without another provider call. Reusing the key for different request content fails before generation. A race in which two brand-new workers miss the lookup can still produce two provider calls, but the database uniqueness constraint permits only one execution; differing generated Plans fail closed rather than replacing the winner.

## Crash and recovery behavior

- **Crash after durable checkpoint:** the completed receipt and checkpoint remain authoritative. After lease expiry, another worker starts the next dependency-ready step; the completed operation is not repeated.
- **Concurrent worker before expiry:** lease acquisition fails and no operation runs.
- **Expired-lease takeover:** a new worker may continue from the latest durable version. A stale expected state version is rejected by CAS.
- **Crash after operation but before result commit:** the receipt remains `started`. Recovery marks it `ambiguous`, changes the execution to `reconciling`, and performs zero retries.
- **Terminal execution:** later workers receive a terminal decision and do not execute steps.

## Test boundary and remaining risks

Tests use a Fake structured Planner, a deterministic authorized sales executor and the existing test-only PostgreSQL schema in a unique disposable database. They cover valid Plan persistence, submission deduplication, request/key conflicts, complete three-step execution, process-crash simulation, replay prevention, lease exclusion, expiry takeover, stale CAS rejection and ambiguous outcomes.

Final local verification passed 357 offline tests and 64 isolated database tests without a paid API call.

The schema is still a test fixture, not a production migration. Planner submission reservation is not persisted before the provider call, so a simultaneous first submission can duplicate model cost even though it cannot duplicate the durable execution. Leases are not heartbeated during long operations. Ambiguous effects still require a human/domain reconciliation workflow. Production identity/RBAC, cancellation, durable provider telemetry, approval policy and distributed operational testing remain future work.
