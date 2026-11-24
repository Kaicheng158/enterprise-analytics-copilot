# PV2-04f — Independent End-to-End Integration Evaluation

Status: local synthetic evaluation complete; uncommitted; no new paid call; no public endpoint or production flag.

## Purpose

PV2-04f evaluates the product/Agent chain independently from the development tests that created each component. A unit or component test passing is not sufficient release evidence. The frozen suite verifies the composed path:

`Session → Private Project → Dataset Revision → Planner → Plan Validator → Durable Runtime → Tool Executor → Checkpoint/Receipt → Trusted Synthesis → Chart/Report → Result Validator`

The evaluation does not introduce another Runtime, Planner, Tool path or result contract. It exercises the committed PV2-03/04 components using synthetic CSV/XLSX data and disposable PostgreSQL.

## Frozen policy

The 15-case suite, policy, evaluator and referenced PV2-04d.4/PV2-04e release hashes are frozen under `eval/datasets/integration-v1/`. The gate was fixed before final evidence:

- Schema: 100%.
- Hard Invariants: 100% for every applicable case; no averaging.
- Core Task Completion: 100%; expected safe rejection is a pass only for a preregistered rejection case.
- Semantic Quality: at least 85% across task completion, relevance/actionability and clarity/concision.
- Unexpected failures: zero.

Failure classes are fixed as Session, Authorization, Dataset Lifecycle, Planner Intent, Plan Schema, Validator, Durable Runtime, Receipt Integrity, Synthesis Grounding, Chart/Report Consistency, Provider, Budget or Evidence Integrity. A mismatched observed outcome must carry one of these classifications.

## Coverage and outcomes

The independent PostgreSQL suite covers:

- English answer, Chinese full report, categorical bar chart and scalar no-chart behavior.
- Finance CSV and People XLSX as separate immutable-revision tasks.
- same-key duplicate submission, completed replay and multi-step post-checkpoint crash recovery.
- expired Session, cross-user access, cross-Project identity and publication-time revocation.
- expired Asset, corrupted private blob, tampered Receipt JSON and changed report number/source.
- a mathematically executable cost plan submitted for a revenue request; exact Validator matching rejects it before execution persistence.

The final saved evidence reproduces a 15/15 gate PASS with Schema, Hard Invariants and Core Completion at 100%, Semantic Quality at 100% and zero unexpected failures. The seven independent disposable-database tests pass. Full compatibility regression passed **537/537 offline** and **197/197 disposable PostgreSQL** tests.

## Defect found during independent evaluation

The Receipt-tamper scenario found one real implementation defect. `PostgresCheckpointStore` correctly rejected a modified Receipt whose stored SHA no longer matched, but `TrustedResultSynthesisService` allowed the internal `ReceiptConflict` exception type to cross its boundary. No tampered result was published, but the error contract was not safely normalized.

The minimal fix maps `ReceiptConflict` to the content-free `synthesis_receipt_integrity_invalid` failure while classifying unrelated store availability failures as `synthesis_receipt_unavailable`. The test still mutates the persisted Receipt and still requires rejection; a separate negative test prevents a database outage from being mislabeled as tampering. No Gate, case, expected result or assertion was weakened.

## Real-model and Fake boundaries

- New PV2-04f provider calls: **0**.
- Planner used in the new integration execution: `DeterministicDatasetPlanProvider` (Fake) only.
- Synthesizer used throughout: `FakeResultSynthesizer` only.
- DeepSeek evidence: immutable PV2-04d.4 historical reference, four calls, 14,044 tokens, 5,650 ms provider latency and USD 0.0048504 recorded cost. It was not replayed or counted as a new PV2-04f run.

Therefore PV2-04f does not establish combined live-Planner plus live-Synthesizer quality, repeated model stability or production readiness.

## Multi-Dataset boundary

The suite validates multiple Dataset structures and revisions through independent tasks and proves their traces/sources do not cross. The current `dataset-plan-v1` deliberately binds one task to exactly one immutable Dataset Revision. Cross-file joins, multi-revision plans and Data Detective correlation are not implemented and must not be inferred from this PASS.

## Feature Value Gate

Decision: **Approve as necessary release-safety infrastructure; not a new user-facing feature.**

It reduces manual end-to-end verification across identity, authorization, durable execution, calculation, provenance and report layers, and prevents releasing results that are mathematically valid but answer the wrong intent. This supports Investigate and Decide & Review; it provides safety prerequisites for future Simulate and Collaborate work.

Preregistered measures:

- one reproducible command for the independent synthetic suite;
- zero accepted authorization, Receipt, numeric, unit or source mutations;
- exact rejection of request-mismatched Planner intent before persistence;
- zero new paid calls and zero production flags/endpoints;
- explicit reproduction steps, component versions and SHA references for every case.

These results are engineering evidence, not measured user time savings.

## AI Agent Engineering learning objectives

1. Design evaluation as an independent system rather than a collection of implementation tests.
2. Separate mathematical Tool correctness from Planner semantic correctness.
3. Propagate identity, immutable data binding and provenance across every Agent stage.
4. Test idempotency and recovery with real durable state, not only mocks.
5. Use adversarial state mutation to validate Receipt and report integrity.
6. Keep live-model evidence, Mock behavior and deterministic behavior explicitly separated.
7. Build fail-closed release gates where safety failures cannot be averaged away by quality scores.
8. Classify failures at the responsible architectural layer before changing code or evaluation.

## Remaining risks

- No new live DeepSeek run and no live LLM Synthesizer evaluation.
- No repeated stochastic stability, load, cancellation, long-running workload or multi-host test.
- One task/one Dataset Revision; no cross-file analysis or join semantics.
- Reports are not durable user-owned report revisions and have no export/collaboration workflow.
- Local single-owner identity, storage and PostgreSQL do not prove production security or availability.
- Synthetic schemas do not cover fiscal calendars, large/high-cardinality data or customer-specific metrics.
- UI and public Product Facade delivery remain disabled.

PV2-04f does not authorize the next product feature automatically. The next proposal must separately pass Feature Value Gate and AI Agent Learning Gate.
