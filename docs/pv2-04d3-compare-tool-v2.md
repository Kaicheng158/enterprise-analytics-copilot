# PV2-04d.3 — Compare Tool v2 and evaluation v3

Status: local offline/Mock/disposable-PostgreSQL implementation complete; not committed, not released, no paid provider call.

## Why this version exists

The immutable PV2-04d.2 live smoke remains **FAIL / Hold**. Its two executable cases preserved the requested January/February scopes in range-filtered aggregate steps, but Compare v1 accepts only two literal values from one dimension. A month range is not a stored date value, so the deterministic Tool correctly returned `filter_value_invalid` instead of fabricating a change. The historical runner also recognized only an `eq` date as explicit scope; that separate evaluator defect is recorded without re-scoring the historical run.

PV2-04d.3 fixes the general contract mismatch. It does not hard-code a month end, a fixture value, a language or a Dataset.

## Compare v1 and v2

| Boundary | Compare v1 (preserved) | Compare v2 |
|---|---|---|
| Registered name | `compare_dataset_revision` | `compare_dataset_revision_v2` |
| Contract version | `1.0.0` | `2.0.0` |
| Selection | shared filters plus two literal dimension values | independent `baseline_filters` and `comparison_filters` |
| Grouping | no grouped alignment | zero to four explicit `group_by` fields |
| Measures | one measure | explicit baseline/comparison measures with compatible units |
| Range semantics | only literal stored values | existing typed filters; `between` is a closed inclusive interval |
| Missing group | not applicable | null plus an explicit limitation; never silently converted to zero |
| True zero | preserved | preserved and distinct from missing/null |
| Zero denominator | derived percentage is null with `zero_denominator` | same fail-closed rule |
| Provenance | immutable Revision, parameters hash, source rows | same, covering both scopes |

The v2 contract permits `sum`, `average` and `count_non_null`, absolute or percentage change, at most twelve filters across both sides, four grouping fields and the existing bounded result/group limits. Decimal arithmetic is used throughout. Incompatible units return centralized clarification. An identical scope and measure is rejected before execution.

## Agent boundary

`dataset-planner-v3` exposes seven registered Tools: the six historical PV2-04c names plus Compare v2. The historical v1/v2 Prompt, schema and Tool catalog hashes remain unchanged. The v3 Draft can select Compare v2, but still carries no Project, Revision, hash, authorization, path, row, SQL or Python authority. Server compilation adds live identity and the existing `DatasetPlanValidator` checks the binding, fields, graph, arguments and budget before the unchanged Durable Runtime can persist a task.

### Control flow, data flow and result references

- **Control flow** says that one step may run only after another. Existing `depends_on` edges provide this ordering.
- **Data flow** transfers a value produced by one step into another step.
- **Result reference** is a typed identifier for such a previous output, including its shape, unit and failure semantics.

The current Runtime provides control flow only. It does not claim that `depends_on` transfers aggregate values. Compare v2 therefore computes both authorized scopes in one deterministic Tool call. A general Result Reference/Data Flow mechanism remains a separate Agent Orchestration learning node and is not introduced implicitly in this stage.

## Evaluation v3

The independent `dataset-planner-eval-v3` suite contains fourteen cases covering the two historical failures, inclusive daily/month/cross-month/cross-year scopes, missing groups, zero/negative/null behavior, unit mismatch, year clarification, invalid fields, authorization, injection and Compare v1 compatibility.

The policy accepts either an exact inclusive `between` filter or semantically equivalent `gte` plus `lte` boundaries. It rejects a single month-end equality as proof of a whole natural month and rejects a range encoded inside one literal value. Hard invariants and Core Task Completion must be 100%; semantic quality retains the provisional 85% threshold and cannot compensate for a hard/core failure.

Frozen identities:

- Prompt `dataset-planner-v3`: `be217af5f6e34e1dd17bf17b0b1112ae208cc41f784f5d7b213a76de40d0a1b0`
- Draft schema: `afd426f1046d10950a8c453928459b24b122c3e1b6b95476a0b710ebd252eff8`
- Suite: `648f7a24c5f09942722f4e08e708449b32a9bcc7c725d833ba2d0db757fb1b4d`
- Policy: `038b150edf0048b71ed71a2a7ab7e8cceca070fc9cd4bcb79b96a44391896169`
- PV2-04d.4 live plan: `889c552598421d9a9fdba265d15e9c6b738b5a86e544f733bfc75231bcfae60e`

## Original Draft replay versus v2 contract conversion

The saved PV2-04d.2 DeepSeek Drafts were replayed **unchanged** through Compare v1 in the historical diagnostic. They still return `filter_value_invalid` and remain FAIL. That record is not re-scored or replaced.

The successful checks below are different: a test-owned Mock v3 Draft expresses the same two user intents with the new Compare v2 contract. This proves that the new Schema → Compiler → Validator → Durable Runtime → Tool path can execute those intents. It does **not** prove that DeepSeek can autonomously produce that Draft; PV2-04d.4 is the first authorized test of that question.

The English case compiles to one `compare_dataset_revision_v2` operation with January and February 2026 inclusive filters, `group_by=[region]`, USD revenue and absolute change. The disposable Runtime returns:

- North: `100.25 → 120`, change `19.75`;
- South: `-20.25 → 0`, change `20.25`.

The Chinese case uses the same provider-independent Tool contract without grouping and returns `80 → 120`, change `40`. Both tasks complete through the existing Queue, Checkpoint, Receipt and authorization path; consistency audit returns `consistent`. These are Mock/offline **contract-conversion** replays, not original-Draft replays and not new DeepSeek evidence.

## Gates and limits

Feature Value Gate: **Approve as necessary infrastructure / user-value evidence pending**. The contract removes the need to manually discover literal date values before comparing supported scopes and prevents silent missing-group/zero errors. No real-user time saving has been measured.

AI Agent Learning Gate: **Approve**. The stage demonstrates Tool-contract expressivity, structured Planner compilation, semantic evaluation, control-flow versus data-flow boundaries, deterministic grounding and backward-compatible contract versioning.

Still unverified: real-model stability, arbitrary calendars/fiscal periods, general cross-step Result Reference, multi-currency conversion, public API/UI, customer data, production isolation and load behavior. All feature flags remain off.

Final local verification passed **518/518 offline tests** and **188/188 disposable PostgreSQL tests**, including 38 focused Compare/Planner compatibility checks and three focused database authorization/Durable replay checks. Two earlier full-database attempts each exposed a different unchanged timing-sensitive test. The one-second Lease test can fail closed as `lease_lost` if host scheduling stalls beyond its test lease; this protects rather than bypasses fencing, but makes the wall-clock assertion load-sensitive. The local product test can exceed its five-second observation window under full-suite contention. Both unchanged assertions subsequently passed 10/10 repetitions and the final unchanged full suite passed. No assertion, timeout or frozen rule was weakened; real-time integration-test sensitivity remains an explicit risk.

## PV2-04d.4 preregistration

The frozen future smoke is one round of four cases, at most four DeepSeek Flash calls, zero retries and USD 0.01 total budget. Configuration remains temperature `0`, thinking disabled, 800 output tokens and 30-second timeout. Offline tokenizer input is 13,244 tokens across four requests; conservative peak maximum is USD 0.0078132. Hard invariants, schema and Core Task Completion must each be 100%; normalized semantic quality must be at least 85%. Passing would be a small smoke only, not a stability or production release claim. Paid execution remains unauthorized in the frozen plan.
