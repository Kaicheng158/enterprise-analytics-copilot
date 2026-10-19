# Phase 5.1 — Bounded Multi-step Planning Foundation

## Scope

Phase 5.1 adds a provider-independent, default-off planning boundary above the existing Phase 4 Tool Registry and Tool Executor. It does not expose a new HTTP endpoint, modify `/chat` or `/rag/chat`, enable an agent loop, or call a paid model.

The first supported task is a deterministic comparison of two sales quarters:

1. query the baseline quarter through `get_postgres_sales_summary`;
2. query the comparison quarter through the same authorized tool;
3. validate that period, region, currency, relation and dataset version are comparable;
4. calculate absolute and percentage change locally with `Decimal` arithmetic.

## Architecture

```text
PlanningRequest
  -> PlanProvider (Fake Provider in Phase 5.1)
  -> strict Plan validation
  -> server-owned planning policy and limits
  -> BoundedPlanExecutor
       -> ToolExecutor -> ToolRegistry -> PostgreSQL sales tool
       -> local compare_sales_growth computation
  -> validated AgentState snapshots
  -> PlanExecutionResult
```

`backend/planning/models.py` owns strict Pydantic contracts for `Plan`, `PlanStep`, `AgentState`, limits, errors and the sales-comparison result. Unknown fields are rejected. Dependencies may only refer to earlier steps, step IDs are unique, and the current task requires one final computation with explicit dependencies.

`BoundedPlanningOrchestrator` accepts one provider proposal, limits its serialized size, validates it locally and verifies that its task scope matches the server request. Provider exceptions and malformed plans fail with sanitized errors before any tool executes.

`BoundedPlanExecutor` executes the already validated plan sequentially. It records pending, running and terminal state snapshots; deep-copies tool outputs before storing them; validates intermediate PostgreSQL evidence; and stops on the first error. Tool output is data and cannot add steps, change policy or become an instruction.

## Server-owned bounds and authorization

The default Phase 5.1 limits are:

| Limit | Value |
|---|---:|
| plan steps | 3 |
| tool executions | 2 |
| provider proposal calls | 1 |
| execution timeout | 15 seconds |
| execution budget | 3 units |
| attempts per step | 1 |

One tool or local computation consumes one budget unit. These are local execution units, not tokens, currency or a billing mechanism.

The planning policy permits only `get_postgres_sales_summary` and `compare_sales_growth`. The existing Tool Registry independently enforces the caller principal and validates typed arguments. SQL remains the Phase 4 fixed, parameterized, read-only query. A plan cannot authorize itself, expose another tool, submit arbitrary SQL or execute code.

`MULTI_STEP_PLANNING_ENABLED` defaults to `false` and invalid values fail closed. No application route loads this runtime. Explicit trusted application code must enable and construct it.

## State consistency

Each `AgentState` snapshot validates:

- unique step IDs and an exact completed-step count;
- one current step only while that step is running;
- tool-call accounting against attempted tool steps;
- budget accounting against all attempted steps;
- terminal error presence for failed states;
- complete execution and no error for a completed state.

Each step runs at most once. A failed or timed-out step stops the plan and every remaining pending step becomes `skipped`. A compute step cannot carry a tool-call ID.

Intermediate sales evidence must use the expected PostgreSQL relation and synthetic dataset version, have nonnegative order counts, use fixed two-decimal money strings, and preserve the existing AOV rule: no matching orders means `average_order_value=null`; matched orders require an AOV value. Growth is rejected when the baseline total is zero.

## Deterministic demonstration

The seeded local dataset produces this all-region comparison:

| Measure | Value |
|---|---:|
| 2026-Q1 sales | USD 400.00 |
| 2026-Q2 sales | USD 600.00 |
| absolute change | USD 200.00 |
| growth | 50.00% |

The result records `analytics.sales_orders` and `synthetic-sales-db-v1` as its source scope. It uses two actual PostgreSQL tool executions followed by one local computation.

## Verification coverage

Offline tests cover strict contracts, ordered dependencies, malformed and failing providers, request/plan mismatch, step/tool/budget limits, unauthorized actions, fail-fast behavior, timeout, invalid intermediate money/AOV evidence, scope mismatch, zero baseline, state consistency, feature-flag fail-closed behavior and absence of a public planning route.

Database tests execute the Q1/Q2 flow against local PostgreSQL and confirm the existing registry still denies an untrusted principal. The full repository offline and database suites are run separately during acceptance. No live LLM or embedding API is used.

Local acceptance on 2026-10-19 passed 297 offline tests and 46 isolated database tests. Docker reported both the API and PostgreSQL services healthy; `/health` and `/health/db` returned HTTP 200. The direct local demonstration completed all three steps with two real PostgreSQL tool calls and the expected 50.00% growth result.

## Known limits and next route

The timeout is cooperative around step boundaries; a blocking dependency still needs its own network/database timeout. Plan and state history are in memory, so there is no restart recovery, durable checkpoint, idempotency ledger, concurrent scheduler or human approval pause. The budget is a simple unit counter. Only one deterministic task and one local computation are supported. Production identity/RBAC, durable audit storage and consequential-action approval remain unresolved Phase 4 production limits.

Recommended Phase 5.2 work is deterministic plan evaluation and durable execution/checkpoint design before introducing a live LLM planner. It should add broader plan-policy tests, cancellation semantics, idempotency keys and resumable state while keeping tool authorization independent. A live provider should only be introduced after a frozen planning evaluation policy and cost/stop controls exist.
