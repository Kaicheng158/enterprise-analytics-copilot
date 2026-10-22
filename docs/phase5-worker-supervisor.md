# Phase 5.7d bounded Worker lifecycle foundation

## Scope and reuse

Phase 5.7d adds lifecycle management around the Phase 5.7c persistent queue. It does not create a second executor, state machine or queue table. `PersistentAgentWorkerSupervisor` hosts a bounded number of existing `PersistentAgentQueueWorker` instances, and every claimed task still runs through the established Lease, Heartbeat, CAS, Checkpoint, Receipt and reconciliation path.

The separate `backend.managed_agent_app:app` profile remains loopback-only and disabled by default. It creates only a disposable test database and is not imported by the normal API, RAG API or Compose services. Enabling the API, queue and supervisor requires three independent server-side flags.

## Lifecycle and shutdown

The supervisor has explicit `created`, `running`, `stopping` and `stopped` states. `start` creates at most the configured number of Worker threads. `stop` first sets the shared stop signal, so no Worker begins another poll, then waits up to the configured grace period for already claimed tasks to finish their existing durable execution.

A grace timeout is reported in metrics and the disposable runtime refuses to delete its database while a Worker may still be writing. The supervisor never kills a thread or treats shutdown as permission to replay work. After a process-level interruption, the existing Lease expiry and Receipt recovery rules decide whether another Worker may continue or must enter reconciliation.

## Queue admission and backpressure

New execution admission is bounded per server-owned scope. PostgreSQL takes a transaction-scoped advisory lock for that scope, checks for an existing HMAC-bound idempotent request, counts nonterminal `pending`, `running` and `reconciling` executions, and inserts only below the configured capacity. A new request at capacity receives the safe HTTP `agent_queue_full` response. A matching idempotent repeat still returns its original task when the queue is full.

This capacity is configured on the Checkpoint Store used by the isolated managed profile. It does not change the frozen Durable Planner implementation or any historical evaluation identity. The check bounds persisted active work; it is not a distributed rate limiter or a reservation before a future paid Planner call.

## Metrics

The in-process snapshot reports:

- lifecycle state and configured/alive/active/peak Worker counts;
- claims and terminal/reconciliation/paused/incomplete/error outcomes;
- idle polls, polling errors and shutdown timeouts;
- authoritative PostgreSQL queue counts for pending, running and reconciling executions.

Metrics contain counters and task-state totals only. They omit prompts, tool inputs/results, API keys and database connection details. They reset when the local process restarts and are not yet exported to an observability backend.

## Safety and verification

The managed profile uses Fake planning, synthetic sales rows and disposable PostgreSQL databases. Tests prove:

- bounded concurrent execution and atomic concurrent admission;
- no loss of unclaimed tasks during graceful shutdown;
- restart of the supervisor completes the persisted backlog;
- idempotent requests are accepted at capacity without duplicate execution;
- unknown post-start outcomes enter reconciliation without another Tool call;
- shutdown timeout is visible and does not force-kill a Worker;
- frozen Planner artifacts remain byte-identical.

Final verification passed 404 offline tests and 106 isolated database tests. No paid provider call, dependency, public deployment or database migration was added.

## Remaining production work

This is a local lifecycle foundation, not a production Worker platform. It still needs an external process manager, workload identity/RBAC, durable/exported metrics and alerts, cancellation semantics, operator reconciliation controls, dead-letter policy, multi-scope fairness, orphan cleanup and deployment-level shutdown probes. The in-process cooperative shutdown cannot prevent a host kill; safety after that event depends on Lease expiry and fail-closed reconciliation. External side effects are not claimed exactly once.

## Difference from Phase 5.7c

Phase 5.7c supplied the durable queue claim primitive and one independently invoked Worker. Phase 5.7d hosts a bounded Worker pool, coordinates its start/stop lifecycle, rejects excess persisted work atomically, exposes safe operational counters and verifies shutdown/restart behavior. The underlying Planner, Tool Executor, database records and recovery semantics are unchanged.
