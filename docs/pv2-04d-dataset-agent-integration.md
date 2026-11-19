# PV2-04d Dataset Agent Integration

Status: local Fake/Mock and disposable-PostgreSQL implementation complete; uncommitted review state. No public endpoint, paid model, customer-data path, chart or report is enabled.

## Purpose and boundaries

PV2-04d keeps the self-built Agent as the primary learning and engineering path. It connects the six PV2-04c deterministic tools to the existing Planner validation, Tool Registry/Executor, PostgreSQL execution queue, Checkpoint, Receipt, Lease, CAS, heartbeat and recovery components. It does not replace those components, add a second task state machine, or introduce Codex, LangChain, LangGraph, OpenAI Agents SDK, MCP, multi-agent routing or memory.

The client-facing intent remains structured and bounded. `dataset-agent-request-v1` binds one server-authorized Project and immutable `DatasetRevisionBinding` to one to six requested operations. Operation arguments cannot contain identity, Project authority, binding overrides, SQL, Python, `trusted_local`, Tool grants or model controls. Free-form natural-language planning remains a later, separately evaluated phase.

## Versioned planning path

The provider-independent `dataset-plan-v1` contains:

- the exact Project, Asset, AssetRevision and DatasetRevision;
- the normalized content SHA-256 and current authorization version;
- the canonical request SHA-256;
- one allowlisted Tool step per requested operation;
- one final local `assemble_dataset_results` step depending on every Tool result.

`DatasetPlanValidator` checks step/tool/budget bounds, graph order, exact request-to-step mapping, six-tool authorization, strict PV2-04c input schemas, fixed binding/content/authorization identity and all referenced fields. Missing fields across operations are returned together before persistence. The deterministic Fake Planner is an adapter for tests, not a privileged execution path; its Plan passes the same validator used by any future provider.

The durable store now parses a version-discriminated union of the historical `bounded-plan-v1` and new `dataset-plan-v1`. Existing sales Plan behavior remains unchanged. Agent state, transition validation, receipt hashes, lease fencing and recovery stay shared.

## Atomic authorization and execution

Submission performs a live Dataset authorization/integrity read, generates and validates the Plan, then repeats the binding check. Execution creation, initial Checkpoint and Project task binding are committed in one PostgreSQL transaction. The binding insert locks and verifies the active User, Session, Project owner and authorization version; a mismatch rolls back the execution. Project-authorized queue stores now require an active binding before claim or lease acquisition.

Every Worker step checks the active task binding. PV2-04c Tool handlers independently check the exact Revision before and after calculation. Revocation or lifecycle change prevents publication. A completed Tool Receipt is not replayed after a post-checkpoint crash. An unknown operation outcome enters reconciliation and is never converted into a fabricated result.

Final assembly revalidates every persisted structured Tool result, preserves all source spans, and consolidates duplicate clarification findings into one result. Mathematical limitations such as a zero denominator remain limitations; they are not silently converted into facts or repeated questions.

## Internal task lifecycle

The local composition supports:

1. strict request submission with scoped HMAC idempotency;
2. atomic execution/task authorization binding;
3. independent queue claim through `FOR UPDATE SKIP LOCKED`;
4. bounded Tool execution and durable Receipts;
5. authoritative queued/running/reconciling/completed/failed status reads;
6. structured result/source retrieval;
7. checkpoint recovery without replay of committed steps.

There is no HTTP route in this phase. `DATASET_AGENT_ENABLED=false` and all existing Agent/Worker flags remain false by default.

## Evaluation coverage

The independently versioned `dataset-agent-eval-v1` suite was frozen before the database run. Its eight cases cover valid finance planning, an independent people/XLSX structure, illegal Tool selection, wrong fields, cross-Project identity, calculation/integrity failure, post-checkpoint crash recovery and idempotent replay. Hard invariants include exact binding, authorization, deterministic result grounding, Receipt/Checkpoint integrity and no duplicate execution. All eight cases were actually executed and passed; the machine-readable acceptance record maps every case ID to the exact offline or disposable-PostgreSQL test that produced its verdict. Merely freezing the suite is not treated as execution evidence.

Synthetic database acceptance verifies expected regional revenue (`220.25` and `-20.25` USD), period absolute change (`40` USD), headcount ranking (`B`, then `A`) and utilization (`82.8571%`). A tampered blob stops in reconciliation with no final result. Revocation leaves the task unclaimable and produces no Tool Receipt.

Final verification passed **492/492 offline tests**, **185/185 disposable PostgreSQL tests** and **11/11 focused PV2-04d tests**. The focused legacy authorization/Durable/Queue compatibility selection passed 47/47. No paid provider was called and no migration was added or applied to the existing application database. Machine-readable evidence is in [the synthetic acceptance record](pv2-04d-synthetic-acceptance.json).

## Feature Value Gate v1

Decision: **Approve as necessary infrastructure**.

- **Work reduced:** supported calculations can be submitted together, executed once and recovered without manually repeating spreadsheet filters, formulas or source capture.
- **Product line:** directly supports Investigate; verified results are prerequisites for Simulate and Decide & Review; immutable Plan/Receipt/source identities support later Collaborate.
- **Non-duplication:** reuses PV2-04c Tools and the existing durable stack. The only new layers are Dataset-specific Plan contracts, validation and composition.
- **Measurable engineering proxies:** one durable task for a multi-operation request; one provider proposal for idempotent repeats; zero duplicate Tool calls after committed recovery; zero cross-scope result publication; all source labels bind the same immutable Revision.
- **Unproven value:** local engineering evidence does not establish real user time saved. PV2-04e usability work must compare this flow with manual spreadsheet analysis.

## AI Agent Learning Gate v1

- **Learning objective:** understand how model/provider output is separated from Plan authority, how a Plan is validated, and how durable state prevents unsafe replay.
- **Implemented principle:** provider-independent structured Plan → deterministic Validator → server-authorized Tool execution → durable state/Receipt → recovery-safe final assembly.
- **Evaluation:** malicious/invalid Plan tests, independent dataset structures, authorization negatives, fault injection, idempotent replay and exact-result checks.
- **Boundary:** this is bounded structured planning, not autonomous open-ended reasoning, long-term memory, multi-agent coordination or production deployment.

## Remaining risks and next boundary

Resource bounds remain cooperative in-process limits. There is no production workload isolation, cancellation API, Human-in-the-loop approval, real LLM Dataset Planner, public task API, arbitrary question support, chart or report. Project collaboration and production storage/identity blockers remain unchanged. PV2-04e may consume only verified `dataset-agent-result-v1` output to build Chart Specs, concise answers and versioned reports; it must not recalculate metrics in presentation code.
