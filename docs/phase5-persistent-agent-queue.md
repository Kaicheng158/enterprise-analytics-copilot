# Phase 5.7c persistent Agent queue and independent Worker

## Purpose and reuse

Phase 5.7c separates HTTP submission from execution. `POST /internal/agent/tasks` validates and persists an execution, then returns `queued`; it does not invoke a Tool. An independently scheduled `PersistentAgentQueueWorker` claims persisted work and runs the existing `DurableAgentRuntime`. `GET /internal/agent/tasks/{task_id}` continues to read the authoritative execution and Checkpoint and can report queued, running, reconciliation, completed or failed state.

No second task table or state machine was added. The existing `agent_runtime.executions` rows are the queue, the existing `(status, lease_expires_at)` index supports polling, and the existing Checkpoints and Receipts remain the execution ledger. There is no new database migration or dependency.

## Atomic claiming and execution

`PostgresCheckpointStore.claim_next_execution` performs one transaction:

1. filter by the server-owned scope and runnable `pending`/`running` states;
2. require an absent or expired Lease;
3. order by creation time and execution ID for stable oldest-first selection;
4. lock one candidate with `FOR UPDATE SKIP LOCKED`;
5. write the bounded Worker Lease and return that execution.

The queue claim does not replace runtime safety checks. The first durable step uses the same Lease owner, then the existing Worker revalidates owner, expiry and state-version CAS before starting and committing. Heartbeats renew the Lease during an operation. Step input is bound to the validated Plan, Receipts deduplicate completed work, and unknown started outcomes enter reconciliation.

`PersistentAgentQueueWorker.run_once` handles one claimed execution; `drain` handles a bounded backlog; `run_forever` is a stop-event-controlled polling loop that can be hosted independently from the HTTP process. Poll/database errors are logged as body-free status metadata and backed off. An unexpected Worker failure leaves the Lease in place, so another Worker cannot immediately replay uncertain work.

## Local isolated profile

`backend.queued_agent_app:app` is a localhost-only, default-off test profile. It requires:

- `INTERNAL_AGENT_API_ENABLED=true`;
- a dedicated internal API key;
- `PERSISTENT_AGENT_QUEUE_ENABLED=true`.

It creates a uniquely named `eac_queue_agent_*` disposable database and composes the existing deterministic Fake Planner, PostgreSQL tools and durable runtime. Its HTTP controller only submits and reads tasks. Tests invoke the queue Worker independently, including in a separate polling thread. The normal `/chat`, `/rag/chat`, Phase 5.7a app and Docker Compose remain unchanged.

## Recovery behavior

- **Duplicate request:** scoped HMAC idempotency returns one execution; Receipts prevent repeated completed steps.
- **Concurrent claim:** row locking plus `SKIP LOCKED` gives one Worker the task; another sees no runnable row.
- **Crash after claim, before step:** no Receipt exists; after Lease expiry another Worker safely continues.
- **Crash after a started Receipt:** after expiry, recovery marks the result ambiguous and stops in reconciliation without calling the Tool again.
- **Operation timeout:** the existing timed Worker records the started operation as ambiguous and requires reconciliation; the queue excludes the reconciling task.
- **Stale Worker:** existing Lease and CAS fencing reject heartbeat, Checkpoint and result writes after ownership/version loss.
- **Concurrent status read:** an execution/checkpoint version mismatch returns the existing safe HTTP 409 response; a retry reads a consistent snapshot.

These controls provide at-least-once task pickup with idempotent durable steps and fail-closed handling of unknown effects. They do not claim exactly-once behavior for external systems.

## Verification and remaining risks

Disposable PostgreSQL and HTTP integration tests cover queued submission, real status/result reads, a separately running polling Worker, concurrent claiming, repeated requests, authorization, scope filtering, pre-step crash takeover, post-start crash reconciliation and operation timeout without retry. Fake/Mock components only are used; no paid API is called and no existing application database is migrated.

Final verification passed 399 offline tests and 103 isolated database tests. The focused Phase 5.7c subset passed all 11 queue contract/integration cases.

This remains a local engineering foundation. It lacks a production process manager, queue partitioning, admission control, cancellation, dead-letter/operator UI, workload identity, observability SLOs and orphan-database cleanup after a host-level crash. The local queue profile uses a disposable database and does not authorize a public deployment. All Agent and queue feature flags default to false.
