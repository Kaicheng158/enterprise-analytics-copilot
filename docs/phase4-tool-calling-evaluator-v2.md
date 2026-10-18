# Phase 4.5 — Tool Calling Evaluator v2

Evaluator v2 is an independent, preregistered evaluation line. It does not modify the DeepSeek tool prompt, provider adapter, tool runtime, v1 suite/policy/gate, v1 raw evidence, or the original v1 FAIL verdict.

## Frozen identity

- Evaluator policy: `tool-calling-evaluation-policy-v2`
- Suite: `tool-calling-suite-v2`
- Suite SHA-256: `a7830d318bc2a51aa7bb6b23ea36b06f63ac45dc0b1914a031f744d67cf7396a`
- Gate: `tool-calling-release-gate-v2`
- Gate canonical SHA-256: `8a4ac436dcda1a958af90994b12c9dfd0e1385d91e23170381758b437ad0935d`
- Prompt: unchanged `deepseek-tool-decision-v1`
- Prompt SHA-256: `af1574d65979199045b6ab2cb8267fae23a3bb1887e48d1a53e51ee9ec682dfb`
- v1 suite SHA-256: unchanged `4f0c8dcfe75dae98d06deac25fff6a2ce1cb743a5f3d5f7d0bd1f2553ad20958`
- Paid calls during this work: 0

`freeze-v2.json` binds the v2 policy, cases, gate and evaluator implementation. `v1-protection.json` separately records hashes for all v1 frozen artifacts and the three historical live rounds.

## Evaluation fixes

### AOV semantics

The v1 lexical rule required the literal word `sales`. Evaluator v2 accepts both `sales` and `revenue`, plus equivalent per-order mean phrasing. It requires a valid per-order mean or revenue/sales divided by orders. Reversed division, totals without a mean, order count alone and multiplication definitions fail targeted tests.

### Authorization and source policy

Evaluator v2 treats these as separate questions:

- `authorization`: was the selected tool server-registered and granted to the principal, or was a denied/unknown call safely rejected?
- `tool_source_policy`: did the call use the source required by the case contract?

Source policy is explicit:

- `get_postgres_sales_summary`: stored/persisted/PostgreSQL-bound data;
- `get_sales_summary`: the fixed deterministic in-memory fixture or a case explicitly bound to it;
- no tool: conceptual answers without company data and prohibited operations.

A registered, granted but source-inappropriate tool now passes authorization and fails source policy. It can still be a hard/core release blocker when the case requires a particular source.

### Typed numeric grounding

Evaluator v2 classifies numeric evidence before comparing it:

- business metrics: total sales, AOV, order count and otherwise unclassified currency amounts;
- context/telemetry: period, UUID, version, latency, token usage and telemetry cost.

Only business metrics participate in sales grounding. A wrong sales amount, AOV or order count remains a hard grounding failure. Identifiers and telemetry numbers remain visible in diagnostics but do not become business claims.

### Quality thresholds and prerequisites

The preregistered 85% threshold applies twice:

1. every case must reach at least 85%, with no zero dimension;
2. every complete round must reach at least 85%.

A high round average cannot conceal a below-threshold case. Hard invariants, core blockers, source-policy violations and case verdict failures cannot be averaged away.

Gate prerequisites are no longer boolean parameters supplied by the caller. The gate accepts a strict evidence object for:

- offline tests;
- database tests;
- default-off `TOOL_CALLING_ENABLED`;
- absence of a public Tool API.

Each check is bound to an on-disk artifact and SHA-256. The gate verifies test-log contents/counts, runtime-boundary JSON contents, path containment and artifact hashes.

## Read-only replay of the nine historical failures

The replay reads the original three rounds and creates diagnostic v2 decisions bound to each original evidence SHA. Replay records are explicitly not release eligible and do not replace v1 verdicts.

| Historical case | Runs | v1 | v2 diagnostic | v2 explanation |
|---|---:|---|---|---|
| `selection/no_tool_answer` | 3 | FAIL | PASS | The revenue/orders AOV definition is correct; no tool or unsupported company fact was used. |
| `grounding/tool_result` | 3 | FAIL | FAIL | Authorization and numeric grounding pass, but the local fixture violates the case-bound PostgreSQL source policy. |
| `telemetry/token_cost` | 3 | FAIL | PASS | Sales metrics match; period, UUID, version and latency are diagnostic context rather than unsupported sales claims. |

Aggregate replay: original v1 `0/9 PASS`; diagnostic v2 `6/9 PASS`. The three remaining failures are the same source-policy issue across the three rounds.

## Verification evidence

- Offline: 242/242 PASS.
- Isolated database: 42/42 PASS.
- Fresh deterministic v2 controls: 7/7 PASS, 100% quality.
- Prerequisite evidence SHA-256: `180bbc2852cf7d77f62d512b133ad1a82a73d96396d17b1f3de165a475feddbc`.
- Local gate status SHA-256: `0f40a84f42ec9867f124602c00d8da5af9208c6287e125200775dcbcad302f64`.
- Current v2 Gate: `NOT_RUN`.
- Only blocker: `minimum_live_runs_not_met`.

The test evidence contains no provider calls. The feature flag remains disabled and no public Tool API exists.

## Remaining risks

- The deterministic language rules are tested against current English cases but are not a general semantic judge. Unseen paraphrases, spelled-out numbers, unusual locale number formats and complex comparative statements may require a later independently reviewed evaluator version.
- Numeric classification covers the current sales contract. It does not by itself validate every possible unsupported non-numeric business claim.
- Source expectations are explicit case contracts. They should not be treated as a universal production routing policy until product data-source rules are defined.
- Historical replay diagnoses existing evidence; it does not demonstrate new model stability or qualify for the v2 release gate.
- The prerequisite receipt proves this local run and file state, not an external CI attestation.

## Next real regression plan

No paid run is part of this change. A future, separately authorized run should:

1. verify `freeze-v2.json`, v1 protection hashes and prerequisite artifacts;
2. rerun the seven deterministic v2 controls;
3. fix the v2 suite SHA, gate SHA, unchanged prompt SHA and DeepSeek Flash configuration;
4. execute three independent complete runs of the nine live cases, with at most two provider calls and one tool execution per case, zero automatic retries and the existing safety/cost stop conditions;
5. write new v2 evidence directories without overwriting v1 or earlier artifacts;
6. require 100% applicable hard invariants, zero core blockers, every case at least 85%, every round at least 85%, no zero dimension and no repeated severe issue.

The previous conservative three-round estimate, USD 0.00628155, can be reused only as a planning estimate; actual cost must be recorded from the future run.
