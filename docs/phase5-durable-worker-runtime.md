# Phase 5.5d — Timed-heartbeat Worker Runtime

## Scope

This stage composes the existing Checkpoint Store, Step Receipts, Lease coordinator, crash recovery and reconciliation service into a real internal Worker Runtime. It remains disabled by default with `DURABLE_WORKER_RUNTIME_ENABLED=false`, has no public API and does not call an LLM. Tests use Fake operations and uniquely named disposable PostgreSQL databases. No migration is applied to the existing application database.

## Execution flow

For one dependency-ready Plan step, the Worker Runtime:

1. uses the existing recovery logic to acquire or reject a bounded lease;
2. creates the `started` Receipt and running Checkpoint transactionally;
3. runs the server-owned operation in a daemon worker thread;
4. renews the lease at a configured interval shorter than the lease TTL;
5. rechecks Worker identity, lease expiry and state version before committing;
6. commits the completed Receipt and Checkpoint through the existing transactional CAS path;
7. releases a non-terminal lease so another worker can process the next step.

The heartbeat carries the post-start state version. A checkpoint advance, expired lease or takeover makes the session stale. Every store write still performs its own transactional owner/expiry/CAS check, so a successful pre-check alone never authorizes a later write.

## Failure and takeover behavior

- A heartbeat/database failure returns `lease_lost` and the old Worker performs no durable write.
- A long operation that exceeds its budget is marked ambiguous while the lease is valid; it is never retried automatically.
- A process crash after `started` or after the operation leaves an in-flight Receipt. After lease expiry, the next Worker changes it to `reconciling` without invoking the operation.
- A crash after the completed Checkpoint leaves durable success. After expiry, takeover begins the next step and does not replay the completed operation.
- An operation error or malformed result is treated as an unknown external outcome because side effects may already have occurred.
- A concurrent Worker cannot execute while a heartbeat keeps the lease live.
- A stale Worker cannot heartbeat, release ownership as another Worker, or write a Checkpoint after takeover.

Unknown outcomes use the existing Phase 5.5c evidence-bound reconciliation. A verified success can resume at the next step; a verified failure terminates the execution. The runtime never infers success from a database uniqueness constraint.

## Verification and limits

Local verification passed 365 offline and 81 isolated database tests. New tests cover a heartbeated long operation, concurrent Worker exclusion, simulated heartbeat database failure, stale writes, crashes before and after an operation, post-Checkpoint restart, operation timeout, evidence-bound reconciliation and continuation without replay.

The operation thread is intentionally not force-killed because arbitrary Python cancellation cannot safely cancel an external side effect. After timeout or lease loss it may finish in memory, but it has no path to persist a result; the execution remains unknown/reconciling. Production tools should use provider idempotency keys and status APIs, plus request timeouts shorter than the lease/operation budget.

Remaining work includes a supervised process/queue loop, graceful shutdown and draining, tool-specific cancellation/status probes, distributed clock/latency testing, operational telemetry and alerts, database-role deployment, human approval for consequential actions, and multi-host chaos/load validation.
