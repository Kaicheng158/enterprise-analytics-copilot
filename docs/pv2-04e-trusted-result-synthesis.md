# PV2-04e — Trusted Result Synthesis

Status: local implementation and synthetic acceptance; default off; no public endpoint; no paid provider call.

## Purpose and boundaries

PV2-04e converts an already completed `dataset-agent-result-v1` into a concise answer, a deterministic chart, or a structured report. It is a presentation and validation layer over the existing Dataset Agent, Durable Runtime, Tool Executor, Checkpoint/Receipt store and immutable Dataset Revision. It is not a second Agent Runtime and it does not recalculate business metrics.

The feature is guarded by `TrustedSynthesisSettings(enabled=False)`. No Compose route, customer upload path, production migration, background process or model call was added. The caller cannot submit a Principal, Project, Dataset Revision, SQL, Tool name, model, authorization version or storage reference.

## Execution path

1. Read the existing Dataset Agent task through its authorized task view.
2. Require a `completed` execution and a `completed` `dataset-agent-result-v1`; queued, running, failed, blocked, clarification and reconciliation states cannot publish.
3. Revalidate the immutable Dataset Revision and current Project/Asset authorization.
4. Require every Step Receipt to be `succeeded`; recompute every canonical output SHA-256 and match the final assembly Receipt to the Agent result.
5. Convert numeric Tool Result cells into additive `trusted-calculation-v2` records. Values, units, dimensions, parameter hashes and source spans are copied rather than recomputed.
6. Ask the provider-independent `ResultSynthesizer` for bounded prose. The current `FakeResultSynthesizer` uses only calculation references and makes no model call.
7. Build deterministic `verified-chart-v1` artifacts when the result shape supports them: one date dimension becomes a line chart, categorical groups become bars, and a bounded same-unit fallback becomes a table. A meaningless chart is not forced.
8. Apply adaptive delivery: explicit user mode wins; otherwise small scalar work returns a short answer, chartable work adds a chart, and six or more calculations produce a full report.
9. Validate answer, Chart Spec, Report, source references, numbers, units, unsupported causality and sensitive output.
10. Recheck authorization, lifecycle, task state and validation immediately before publication. Reading an already built response requires the same checks again.

## Versioned contracts

- Reused unchanged: `AnalysisSourceReference`, `CalculationValue`, `ChartSpecV1`, `AnalysisReportV1`.
- Additive: `TrustedSynthesisRequestV1`, `TrustedCalculationV2`, `TrustedClaimV1`, `TrustedAnswerV1`, `SynthesisTraceV1`, `TrustedSynthesisResponseV1`.
- Provider interface: `ResultSynthesizer` receives only the bounded request and verified calculation records.
- Validator: `trusted-result-validator-v1` is independent of the synthesizer.

`TrustedCalculationV2` is necessary because the six PV2-04c operations include dimensions, null/undefined values, parameter hashes and additional operation names not represented by the frozen `deterministic-calculation-v1`. The historical contract is not modified.

## Trust and safety invariants

- Tool output is untrusted data until its strict schema and Receipt hash pass.
- A fact claim must reference exact calculation values and units.
- Every calculation and chart must use source labels derived from current Tool Result source spans.
- Numeric or unit changes, missing/fabricated sources, mixed-unit chart series and unsupported causal wording fail closed.
- Instruction-like values such as `SYSTEM` remain chart/category data and are never promoted to instructions.
- Internal storage paths, keys, session secrets and authorization headers are rejected from output.
- Revocation, expiry, deletion, corruption, task mutation or reconciliation blocks publication/read.
- This does not claim exactly-once external effects; PV2-04e consumes the existing Receipt-backed deterministic analysis path only.

## Frozen evaluation v1

The independent 11-case suite and policy are under `eval/datasets/synthesis-v1/`. They cover short answer, full report, bar/line choice, numeric tampering, unit errors, unsupported causality, missing sources, malicious Tool Result content, revocation/expiry and legacy compatibility.

The preregistered gate is:

- Schema: 100%.
- Hard Invariants: 100%.
- Core Task Completion: 100%.
- Semantic Quality: at least 85% across completeness, relevance and clarity.

The threshold is provisional local engineering policy, not a production-quality guarantee. It was frozen before acceptance evidence and is not changed to fit results.

Local acceptance passed **531/531 offline tests** and **190/190 disposable PostgreSQL tests**, including 13 focused synthesis/gate tests and two full Dataset Agent → Receipt → synthesis database tests covering revocation, expiry and corrupted content. The frozen local gate recorded Schema, Hard Invariants and Core Completion at 100% and Semantic Quality at 100%. This is deterministic synthetic evidence only.

## Feature Value Gate

Decision: **Approve as necessary product and Agent infrastructure; measured user outcome remains pending.**

Baseline workflow: a user or developer must inspect raw Tool JSON, manually copy numbers into prose and chart structures, attach sources, then cross-check values and authorization. The module reduces that flow to one bounded synthesis request while preserving exact Tool/Receipt/source references.

Preregistered product measures:

- zero manual re-entry of published calculation values in the supported synthetic flow;
- zero accepted numeric/unit/source mutations in the hard-invariant cases;
- one synthesis request for an explicit short answer, chart or report;
- no new task or Tool execution when UI/answer presentation changes;
- publication blocked after authorization/lifecycle loss.

Engineering tests establish the mechanism, not actual user time savings. Representative user testing, accessibility/usability review and a real product workflow remain required before claiming reduced user effort.

Product-line connection: Investigate gains evidence-linked answers and charts; Decide & Review gains a traceable report foundation; Simulate is not implemented; Collaborate can later reference immutable Task/Revision/Receipt identities but receives no sharing feature here.

## AI Agent Learning Gate

Learning outcome: separate planning/execution truth from presentation, preserve provenance across Agent stages, design provider-independent structured synthesis, and enforce deterministic post-generation validation.

Key principles:

1. A model or synthesizer is not a source of business truth; verified Tool Results are.
2. Checkpoint and Receipt evidence controls whether a result may be presented.
3. Adaptive output is a policy decision over verified data, not a new analysis operation.
4. Structured schema validity is necessary but insufficient; semantic consistency requires cross-field validation.
5. Authorization is time-dependent and must be rechecked at publication and read.
6. Control flow, calculation provenance and presentation are distinct architectural layers.

## Remaining risks and PV2-04f conditions

- The Fake synthesizer demonstrates the boundary, not natural-language quality or model stability.
- Numeric prose validation is deliberately narrow; localization, currency formatting, rounding policy and richer narrative need versioned contracts.
- Chart selection supports bounded shapes only; multi-axis, mixed-unit and high-cardinality views remain unsupported.
- Responses are not persisted as independently versioned report artifacts; read validation currently applies to the in-memory response.
- No cancellation, report export, Workspace integration, collaboration or production telemetry was added.
- Production readiness still requires threat review, production identity/storage, retention, audit/monitoring, policy-aware previews, accessibility and repeated evaluation.

PV2-04f should be separately scoped after review. It must not silently introduce a live LLM, public endpoint or Workspace result publication without a versioned prompt/provider contract, privacy review, cost guard, frozen evaluation and explicit authorization.
