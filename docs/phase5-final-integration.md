# Phase 5.6 controlled Planner and Durable Runtime integration

## Implemented chain

Phase 5.6 verifies this internal path:

`DeepSeekStructuredPlannerTransport (Mock HTTP) → StructuredPlanProvider → Plan schema/request binding/PlanValidator → DurableControlledPlanner → DurableWorkerRuntime → authorized PostgreSQL Tool Executor → Checkpoint/Receipt → recovery → SalesGrowthComparison`

The new composition root wires existing modules together. It does not create a second validator, executor, state machine, persistence store or recovery implementation.

## Controls verified

- The actual DeepSeek adapter serializes the frozen Planner system prompt, JSON response mode, `deepseek-flash`, thinking disabled, 800 output tokens and zero retries. HTTP is mocked and the test key is synthetic.
- Provider cost reserve can stop execution before HTTP or persistence.
- Unauthorized tools and Plans exceeding the three-step bound are rejected before persistence and Tool execution.
- The server-owned Registry and Executor remain the only path to the fixed read-only PostgreSQL sales query.
- A scoped duplicate request returns the existing execution before a second Planner call.
- A crash after a committed Checkpoint leaves one successful Receipt. After lease expiry, a fresh runtime resumes the next step without asking the Planner again or replaying the completed tool step.
- The final structured result is Q1 USD 400.00, Q2 USD 600.00, absolute change USD 200.00 and growth 50.00%, from `analytics.sales_orders` dataset `synthetic-sales-db-v1`.
- Provider usage and estimated cost remain available from the existing `StructuredPlanProvider.last_response`; the mock run recorded 800 tokens and USD 0.00051 under the existing cost guard.

## Verification and isolation

Local verification on 2026-10-21 passed 372 offline tests and 89 isolated database tests. The Phase 5.6 focused set contains four offline contract/error tests and two disposable-database end-to-end tests. No real provider request was made.

`LLM_PLANNER_ENABLED`, `DURABLE_LLM_PLANNER_ENABLED`, `DURABLE_WORKER_RUNTIME_ENABLED` and `DURABLE_AGENT_RUNTIME_ENABLED` remain false by default. No Planner or Agent endpoint exists. Integration database tests use a uniquely named temporary database, apply migrations and synthetic seed data there, and drop it afterward. The existing application database is not migrated.

## Remaining risks

The adapter-to-runtime chain has not had a new live DeepSeek run; Phase 5.4 evidence establishes the Planner adapter's earlier bounded live behavior, while Phase 5.6 establishes deterministic composition through Mock HTTP. There is no production Worker queue/supervisor, graceful shutdown protocol, distributed cancellation, operator reconciliation workflow, human approval boundary, production identity/RBAC, tenant-isolated credentials, durable provider telemetry ledger or production migration/deployment procedure. External tool effects still require authoritative reconciliation when their outcome is unknown; the system does not claim exactly-once execution.
