# Phase 5.6b durable Planner live-acceptance plan

## Status and scope

Phase 5.6b is preregistered and zero-paid preflight ready. No Phase 5.6b DeepSeek request has run. The release gate is `BLOCKED_PENDING_LIVE_ROUNDS` until two independently recorded rounds pass the frozen rules.

The evaluation reuses the Phase 5.6 composition unchanged:

`DeepSeekStructuredPlannerTransport → StructuredPlanProvider → Plan schema/request binding/PlanValidator → DurableControlledPlanner → Tool Registry/Executor → PostgreSQL sales Tool → DurableWorkerRuntime → Checkpoint/Receipt → final SalesGrowthComparison`

There is no public endpoint. `LLM_PLANNER_ENABLED`, `DURABLE_LLM_PLANNER_ENABLED`, `DURABLE_WORKER_RUNTIME_ENABLED` and `DURABLE_AGENT_RUNTIME_ENABLED` remain false. The live runner additionally requires `DURABLE_PLANNER_LIVE_ACCEPTANCE_ENABLED=true` and the exact confirmation `RUN_DURABLE_PLANNER_V1_LIVE`; neither is enabled by default.

## Frozen identities

- Suite SHA-256: `c059028e3b67924338bcb27ab5cf5c8f1bf7c32977ab63ab217c2d98c061df11`
- Gate SHA-256: `a2355516e49e248e75915d3f7b94c8bf13df4cf19fee1ec592373c872fa1a692`
- Live-plan SHA-256: `425a2613b798ab4add2894aea06a91bfc7d9c4848249422cb2f3a5fd14b0d3a7`
- Planner prompt: `bounded-planner-v1`, SHA-256 `23a1003075c87466b81525f3981a7aca75d97e64c2d1fbd349d5d40f6c39882b`
- Plan schema SHA-256: `8ca0bc1a0a28bc75597bdd77ab56d3878260cbc8c82206c75d2a4cb9f87e5660`
- Historical Phase 5.4 freeze is hash-protected and is not rewritten or re-evaluated.

## Cases and expected results

All data is synthetic and uses `analytics.sales_orders`, dataset `synthetic-sales-db-v1`, currency USD.

1. All regions, 2026-Q1 → 2026-Q2: 400.00 → 600.00; +200.00; +50.00%.
2. North, 2026-Q1 → 2026-Q2: 200.00 → 300.00; +100.00; +50.00%.
3. South, 2026-Q2 → 2026-Q1: 300.00 → 200.00; -100.00; -33.33%.

Every case must produce a strict request-bound three-step Plan: two authorized `get_postgres_sales_summary` calls followed by the local `compare_sales_growth` calculation. Submission is repeated with the same scoped idempotency key and must reuse the execution without another provider call.

## Release rules

Hard invariants are Plan schema validity, request binding, Tool authorization, execution bounds, one provider call, idempotent submission, durable completion, consistent Receipts, exact result accuracy and source consistency. Every applicable invariant must pass in every case and round. Any core completion or result-accuracy blocker fails the gate.

Quality is scored on task completeness, Plan relevance/minimality and description clarity, each 0–2. Every case and every round must reach the preregistered 85% normalized threshold, and no repeated severe quality issue is allowed. This threshold is a provisional Phase 5 engineering threshold, not a universal reliability standard.

Two complete independent rounds are required. One failed round fails the gate; a partial run cannot pass.

## Provider and budget plan

- Provider/model: DeepSeek / `deepseek-flash`.
- Thinking: disabled.
- Maximum output: 800 tokens.
- Provider interactions: one per case, six total across two rounds.
- Automatic retries: zero.
- Per-call safety reserve: USD 0.002.
- Maximum reserved cost: USD 0.012.
- Hard total budget: USD 0.02.

The earlier frozen Planner acceptance averaged about USD 0.000640 per comparable call, implying an observational estimate near USD 0.00384 for six calls. At approximately 918 cache-miss input tokens and the full 800-token output allowance, the current peak-rate catalog yields a more conservative ceiling near USD 0.00741. Both are estimates rather than provider billing. The separate USD 0.012 reserve controls execution even if outputs are larger.

## Zero-paid preflight and stopping rules

Preflight verifies frozen hashes, all offline/database tests, disabled feature flags, no public Planner/Agent route, local key presence without recording it, safe unused evidence paths and cost reserve. It creates a uniquely named temporary database, applies the existing migrations and synthetic seed, verifies `SELECT 1`, eight expected rows, the `agent_runtime` schema and the authorized PostgreSQL sales Tool, then drops that database.

The recorded `preflight-v1-001` result on 2026-10-22 is `READY`: 377 offline tests and 90 isolated database tests passed, every preflight check passed, and paid API calls remained zero. The current release gate is still blocked solely because the two required live rounds have not run.

Before every paid request the live runner repeats the isolated database health/tool check. It stops on database failure, frozen or historical identity drift, a forbidden Tool reaching execution, provider-call/cost limit, missing cost evidence, secret/header detection or an existing evidence path. Evidence is write-once and contains only synthetic requests, generated Plans, results, safe telemetry and verdicts.

## Remaining risks

Preflight proves configuration and local control flow, not live model quality or production reliability. The future two-round sample is small. The Planner still has no production Worker supervisor, graceful shutdown/cancellation, identity/RBAC, tenant credential isolation, durable provider telemetry ledger, operator reconciliation UI or human approval boundary. The test uses disposable local PostgreSQL and read-only synthetic Tools; it does not validate production migration or infrastructure behavior. Unknown external outcomes still require authoritative reconciliation and are not claimed to be exactly once.
