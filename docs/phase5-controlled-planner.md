# Phase 5.4a — Controlled Structured Planner Foundation

## Scope

This increment prepares a provider-independent LLM planning boundary without adding a live DeepSeek client or calling a paid API. A model-compatible transport may generate exactly one structured `Plan`; it cannot execute tools, grant authorization, set limits or write agent state.

The capability remains internal and requires two independent server-owned flags:

- `MULTI_STEP_PLANNING_ENABLED=true` enables the existing bounded execution runtime;
- `LLM_PLANNER_ENABLED=true` enables the structured planner composition.

Both default to `false`. No public endpoint reads either capability, and the current `/chat` and `/rag/chat` behavior is unchanged.

## Control flow

```text
strict PlanningRequest
  -> server-owned bounded-planner-v1 prompt + Plan JSON schema
  -> PlannerTransport (Fake only in this increment)
  -> one structured JSON response, no retry
  -> strict local Plan parsing
  -> request-scope match
  -> existing PlanValidator
       -> dependency/cycle/order checks
       -> server-owned action policy
       -> step/tool/budget limits
  -> existing BoundedPlanExecutor
       -> ToolExecutor / Registry authorization
       -> parameter validation and fixed read-only SQL
       -> guarded AgentState transitions
```

`StructuredPlanProvider` is single-use and permits one provider call. It accepts only the configured provider/model, requires `finish_reason=stop`, rejects oversized or malformed responses, and does not repair invalid JSON. Provider and protocol failures become a sanitized `plan_provider_error`; invalid Plan content becomes `plan_invalid`.

## Server-owned generation contract

The generation request contains:

- prompt version `bounded-planner-v1` and SHA-256;
- the strict `Plan` JSON schema and schema SHA-256;
- a system message that limits the model to planning only;
- a user message containing only the strict planning request fields;
- fixed foundation config: DeepSeek Flash identity, temperature 0, thinking disabled, one provider call, zero retries, bounded output and timeout.

The model never receives caller principal, authorization state, execution budget controls, checkpoint state, tool results, database credentials or API keys through this contract. Generated fields cannot override server limits because unknown Plan fields are rejected and execution policy remains local.

## Verification

Fake transport tests cover:

- valid structured Plan through the complete bounded executor;
- exact server-owned prompt/schema identities and user-message scope;
- malformed JSON and extra control-field rejection;
- request-scope mismatch;
- unauthorized tool proposal;
- budget rejection before tool execution;
- provider exception, truncation and one-call limit;
- independent default-off flags and invalid flag values;
- absence of public planning routes;
- composition without network or database access.

Local database integration uses the same Fake transport with the real Tool Registry and PostgreSQL sales tool. It verifies the authorized Q1/Q2 result and confirms an untrusted principal is still denied independently of the generated Plan. No production migration or paid provider is involved.

Final local verification passed 330 offline tests and 59 isolated database tests. The Phase 5.4a focused subset passed eight offline and two database integration tests.

## Remaining work before a real DeepSeek run

Phase 5.4b should implement a dedicated DeepSeek `PlannerTransport` using official structured JSON output, strict response parsing, no automatic retry, sanitized token/latency/cost telemetry and a hard total-cost cap. Before any paid call, freeze a small planner evaluation suite and release gate covering valid plans, invalid/over-budget plans, unauthorized actions, request mismatch, prompt injection attempts, deterministic controls and execution grounding.

A live planner should remain local and default-off until the frozen gate passes. A generated plan must never bypass `PlanValidator`, Registry authorization, execution budgets, state transitions or Phase 5.3 recovery rules.

## Phase 5.4b — Frozen evaluation and DeepSeek adapter

Phase 5.4b implements the provider transport but does not enable or call it. `DeepSeekStructuredPlannerTransport` sends the two server-owned messages through DeepSeek Chat Completions JSON mode with `deepseek-flash`, thinking disabled, an 800-token output cap, a 30-second timeout, one call and zero retries. It requires a non-empty local secret and the exact frozen generation configuration. Provider output must contain one assistant message, no tool calls, valid usage totals and a terminal `stop` result before its content can reach `StructuredPlanProvider` and the existing local controls. Error messages are sanitized and neither request bodies nor authorization headers are persisted.

The adapter does not send `temperature`: DeepSeek documents that temperature has no effect for Flash when thinking is disabled. This is recorded as a runtime property rather than presented as a stability control. The response reports provider/model, finish reason, token usage, latency, retry count, pricing version and a conservative peak-price estimate. Missing/invalid cost evidence, a response over the reserved cost, transport/protocol failure, an in-flight duplicate, more than nine total requests, or a projected total over USD 0.02 permanently stops the shared guard.

## Frozen Planner evaluation v1

The preregistered assets live under `eval/planning/v1/`:

- suite SHA-256: `60881b4764c4e880afcea589b4373fc1ee6cc92c4cb3ae84305907f9fadee7f8`;
- gate SHA-256: `164d0985b5e05159f1b26d1d0befad5002ccbaabecb37f54483fa861d69b40f7`;
- prompt `bounded-planner-v1`: `23a1003075c87466b81525f3981a7aca75d97e64c2d1fbd349d5d40f6c39882b`;
- Plan schema: `8ca0bc1a0a28bc75597bdd77ab56d3878260cbc8c82206c75d2a4cb9f87e5660`.

Ten frozen cases cover ordinary all-region/north/Q1-to-Q3 planning, unknown and cyclic dependencies, an unauthorized tool, invalid arguments, execution-budget rejection, injected control fields, and instruction-like text inside an untrusted step description. Applicable schema/outcome, provider-call, tool-execution, authorization, budget, injection and state-consistency invariants require 100% pass and cannot be averaged away. A missing requested task is a core blocker. Applicable task-completeness, relevance and clarity dimensions use 0/1/2 scores with an 85% case/round threshold fixed before any paid run; this is a provisional Phase 5 engineering threshold.

The deterministic Fake suite passed 10/10 with zero provider API calls. The release gate is intentionally `BLOCKED`, with `minimum_live_rounds_not_met` as its only blocker. A future release decision requires three independent rounds of the three eligible positive cases using the exact frozen identity and no automatic retry.

## Non-paid preflight and cost plan

`planner-preflight-v1` is READY and records only safe status metadata. It verified all frozen hashes, 345 passing offline tests, 59 passing isolated database tests, `SELECT 1`, the `analytics.sales_orders` relation, eight synthetic rows, the real authorized PostgreSQL tool, all planner flags disabled, no public planner route, a locally configured API key without exposing it, a safe unused evidence target and reserved cost below the hard cap.

The frozen execution plan permits nine total provider requests: three cases across three rounds. Each request reserves USD 0.002, so the maximum reservation is USD 0.018 under the USD 0.02 cap. The current pinned estimate uses 894 input tokens and an 800-token maximum output at the conservative peak cache-miss/output rates, approximately USD 0.0012282 per call and USD 0.0110538 for nine calls. This is an estimate, not billed cost. Any database-health failure, identity drift, security/authorization violation, missing cost evidence, cap breach, secret leakage or existing evidence target stops the run.

No paid call occurred in Phase 5.4b. `MULTI_STEP_PLANNING_ENABLED`, `LLM_PLANNER_ENABLED` and `LLM_PLANNER_LIVE_REGRESSION_ENABLED` remain false; there is no public endpoint. Phase 5.4c may perform the preregistered run only after separate authorization, and a passing gate would still establish local synthetic acceptance rather than production reliability.

## Phase 5.4c result

The separately authorized frozen run completed three rounds of the three live-eligible cases. All nine generated plans were schema-valid, used only the authorized PostgreSQL sales tool, matched the requested periods/region, executed exactly two tool calls plus one local calculation, and returned the expected database-backed comparison. Each round had zero core blockers and 100% normalized quality; the final frozen Gate verdict is PASS. Exact plan identifiers and descriptions varied, while the authorized actions, dependencies, data scope and outputs remained behaviorally stable.

The nine calls used 8,259 input and 2,734 output tokens, 14,299 ms total provider latency and estimated USD 0.0057585. No retry or safety stop occurred. Full write-once evidence is retained under `eval/planning/v1/`; the original preregistration BLOCKED status remains as historical evidence. See [Phase 5.4c acceptance](phase5-planner-live-acceptance.md).
