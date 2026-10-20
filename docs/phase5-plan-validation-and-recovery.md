# Phase 5.2 — Deterministic Plan Validation and Recovery Design

## Implemented scope

Phase 5.2 strengthens the default-off Phase 5 planning package without adding an endpoint or a live model. It adds:

- a side-effect-free `PlanValidator` with a canonical plan SHA-256;
- explicit unknown-dependency, cycle and declared-order checks;
- server-owned action and argument binding for the quarterly comparison task;
- step and agent state-transition guards;
- safe handling for raised exceptions and malformed results from a tool executor;
- deterministic Fake Provider tests and local PostgreSQL regression coverage.

Phase 4 orchestration, `/chat`, `/rag/chat`, Prompt releases and frozen evaluation evidence are unchanged. No paid provider is called.

## Plan validation policy

The validator uses a fixed precedence so the same plan always produces the same first failure:

1. step, tool-call and budget limits;
2. references to unknown dependency IDs;
3. dependency cycles, including self-cycles;
4. dependencies declared after their consumer;
5. server-owned tool and computation authorization;
6. exact task/action binding.

Cycle detection uses deterministic Kahn topological sorting. Ready nodes are ordered by their declared position. A valid graph must produce the same order as the submitted step tuple; a different but acyclic ordering is rejected rather than silently reordered.

For `compare_quarterly_sales`, exactly the two computation source steps must be tool steps. Each must call `get_postgres_sales_summary` with exactly the bound `period` and `region`; extra or substituted arguments fail before execution. The final computation must depend on both distinct source steps and match the plan scope.

The canonical plan hash is calculated from strict model output serialized as sorted compact JSON. It gives checkpoint and evaluation code a stable identity for the exact plan content. It is not a signature and does not grant authorization.

## State-transition policy

Step transitions are limited to:

| From | Allowed next states |
|---|---|
| pending | running, failed, skipped |
| running | completed, failed |
| completed | none |
| failed | none |
| skipped | none |

Agent transitions are limited to `pending -> running/failed` and `running -> running/completed/failed`. Completed and failed agent states are terminal.

The guards also enforce stable plan/step identity, monotonic attempt/counter/time values, append-only completed steps, stable step ordering, rejection of no-op snapshots, and valid per-step transitions inside every agent snapshot. A completed step cannot run again within the current in-memory execution.

The executor remains fail-fast. A tool exception, malformed `ToolExecutionResult`, validation failure, timeout, authorization failure or invalid intermediate result stops the plan and skips remaining pending steps with a sanitized error.

## Checkpoint persistence design

This section was the Phase 5.2 design. Phase 5.3 now implements it in a disposable test-only PostgreSQL schema; there is still no production database table or migration. See [Phase 5.3](phase5-checkpoint-persistence.md).

### Execution record

A durable execution record should persist:

- server-generated `execution_id`;
- scoped idempotency identity: tenant, authenticated principal, task and a hashed client idempotency key;
- request hash, canonical plan JSON, plan version and plan SHA-256;
- policy, limits, tool-catalog and runtime-code versions;
- current `AgentState`, state version and last durable transition number;
- step/tool/provider call and budget counters;
- created, started, updated, lease-expiry and terminal timestamps;
- sanitized terminal error code and status;
- cancellation or human-approval state when those features exist.

The request and plan identities must be bound to the authenticated scope. Reusing an idempotency key with a different request hash must return a conflict rather than reuse or overwrite an execution.

### Step receipt

Each step requires a receipt uniquely keyed by `(execution_id, step_id)`. It should include:

- step kind and action/tool version;
- dependency IDs and their result hashes;
- input/argument hash, output/result hash and protected result reference;
- server-generated per-step idempotency token;
- status, attempt number, start/finish time, latency and sanitized error;
- tool call ID and upstream idempotency receipt when available;
- source-scope metadata needed to validate later computations.

Sensitive inputs and outputs should be encrypted or stored by protected reference with retention controls. Routine telemetry should contain hashes and metadata rather than business content.

### Atomic transition rule

Claiming work and saving its result must use optimistic concurrency on `state_version` plus a short worker lease:

1. atomically claim an eligible execution if its lease is absent or expired;
2. validate the stored plan hash, versions and checkpoint contract;
3. select only a dependency-ready pending step;
4. create or read its unique step receipt;
5. execute with the server-derived idempotency token;
6. atomically write the receipt, new `AgentState`, counters and next state using compare-and-swap;
7. release or renew the lease.

If compare-and-swap fails, the worker must discard its proposed transition and reload. It must never merge counters or tool outputs from two competing workers.

## Recovery algorithm

1. Authenticate and authorize the caller before looking up an execution.
2. Load by scoped idempotency identity and verify request, plan, policy and runtime hashes.
3. Return the stored result for an already completed execution; return the same terminal error for a failed or cancelled execution.
4. Reject a second worker while a valid lease exists. After lease expiry, acquire it with compare-and-swap.
5. Revalidate the stored `AgentState` and every receipt relationship.
6. Never rerun a completed step with a valid success receipt. Rehydrate its protected intermediate result and continue from the first dependency-ready pending step.
7. For an interrupted running step, reconcile the upstream idempotency receipt before deciding whether it completed.
8. If outcome is unknown for a consequential/non-idempotent tool, pause for reconciliation or human review. Do not replay it automatically.
9. Reapply current authorization before every resumed tool execution; a historical checkpoint does not preserve permission.

The current sales tools are read-only, so a repeated query has no side effect. They should still use receipts to keep counters, audit records and returned evidence stable.

## Idempotency boundaries

The API-facing idempotency key should be opaque, length-limited and stored only as a keyed hash. Its uniqueness scope should include tenant and principal. The server derives a separate step token from execution ID, exact plan SHA, step ID and attempt policy.

Database uniqueness prevents duplicate receipts, but it cannot by itself guarantee exactly-once behavior across a network boundary. Consequential tools must support an upstream idempotency key or a transactional outbox/reconciliation protocol. Without one, the safe recovery outcome for an ambiguous in-flight action is `paused`, not automatic replay.

Provider planning calls also need a recorded request hash and accepted-plan receipt. Recovery should normally reuse the already validated plan rather than ask a nondeterministic provider to plan again.

## Remaining work and risks

- A test-isolated checkpoint store and crash simulator now exist; no production migration, lease service or resume API exists.
- Intermediate results remain in memory and are not encrypted at rest.
- Timeout enforcement is cooperative around steps; dependency-specific timeouts remain required.
- There is no cancellation, paused/approval state, retention policy or operational reconciliation queue.
- Canonical hashes detect identity changes but are not authenticity signatures.
- The current executor supports one deterministic task and no live LLM planner.

Phase 5.3 completed the isolated checkpoint repository and crash/recovery simulator in a disposable database. It proves compare-and-swap, lease expiry, receipt uniqueness, completed-step replay prevention and request-hash conflicts before any production migration or live planner integration.

## Local verification

Phase 5.2 acceptance passed 311 offline tests and 46 isolated database tests. The focused planning set covers valid graphs, unknown dependencies, self/two-node cycles, acyclic forward dependencies, stable plan hashes, argument/source binding, legal and illegal state transitions, exact timeout boundaries, raised tool/computation exceptions, malformed Tool Results and pre-execution rejection with zero tool calls. No paid API was called.
