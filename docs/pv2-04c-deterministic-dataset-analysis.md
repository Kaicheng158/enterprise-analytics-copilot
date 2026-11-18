# PV2-04c Deterministic Dataset Analysis Tools

Status: local synthetic implementation and acceptance complete; uncommitted review state. No public analysis API, Planner, Agent, chart, report or customer-data path is enabled.

## Scope and version boundary

PV2-04c adds an internal, default-off deterministic calculation layer over one exact immutable `DatasetRevision`. It reuses PV2-01 parsing contracts, PV2-02 verified private persistence/lifecycle barriers, PV2-03 live Session/Project authorization, PV2-04a revision binding and the existing server-owned Tool Registry/Executor. It does not change `dataset-analysis-task-v1`, the synthetic sales tools, `/chat`, `/rag/chat`, Agent APIs or frozen evaluation evidence.

The new contracts are separately versioned as `deterministic-dataset-analysis-v1`, with `dataset-analysis-tool-result-v1` and `dataset-analysis-source-v1`. The client/model cannot provide a Principal, Project scope, authorization version, `trusted_local`, SQL, Python, Tool authorization or filesystem reference. A server composition captures the live Principal and Project, then registers only six allowlisted tools for the internal `dataset-analysis-runtime` principal.

## Tool catalog

| Tool | Deterministic capability |
|---|---|
| `profile_dataset_revision` | column kind/unit plus row, non-null, null and distinct counts |
| `aggregate_dataset_revision` | typed filters, group-by, count/sum/min/max/average, bounded stable sorting |
| `compare_dataset_revision` | two explicit dimension values with absolute or percentage change |
| `derive_dataset_metric` | difference, ratio or percentage over two compatible aggregated fields |
| `rank_dataset_revision` | bounded highest/lowest dimension ranking by one aggregate |
| `inspect_dataset_quality` | null counts/rates and parser-quality evidence |

Filters support explicit equality, inequality, ordering, membership, range and null checks according to the field type. Text/boolean ordering is rejected. Dates must be ISO `YYYY-MM-DD`; ambiguous dates are returned as one centralized clarification rather than guessed. Decimal and money operations use Python `Decimal`, preserve negative values, reject non-finite values, retain explicit units and never divide by zero. An all-null numeric aggregate remains null rather than being misreported as a real zero. A zero denominator produces a null derived value plus `zero_denominator`; incompatible units produce `unit_mismatch` and no calculation.

## Authorization, integrity and lifecycle

The runtime performs these checks for every tool invocation:

1. resolve the server-owned Session Principal and bound Project;
2. authorize the exact Asset;
3. require exact Project/Asset/AssetRevision/DatasetRevision identity and active/ready lifecycle states through a fixed query;
4. load through the private persistence adapter, enforcing opaque reference, byte length, SHA-256 and Dataset contract integrity;
5. execute only the allowlisted in-memory operation;
6. reauthorize the Asset and compare the authorization version immediately before returning.

Cross-user, cross-Project, forged revision, expired Session, revoked Project, expired Asset and tampered blob tests all fail closed. A revocation injected between read and publication prevents the result. No internal storage reference or path appears in output or telemetry.

## Resource and result limits

`DatasetAnalysisSettings` is immutable and bounded: 5,000 input rows, 200 groups, 200 result rows, 12 filters, 8 aggregations and a five-second absolute maximum (two seconds by default). Existing PV2-01 file/cell limits remain authoritative. The analyzer checks its deadline while scanning rows. Source spans preserve Project/Asset/Revision identities, normalized SHA-256, table/sheet, row range and referenced columns. Results include a canonical parameter SHA-256 so repeated requests can be compared without logging data values.

The current implementation is an in-process trusted service. It is not an OS sandbox and does not claim hard memory or CPU isolation. Production worker isolation, cancellation, scheduling, monitoring and capacity controls remain release blockers.

## Synthetic calculation evidence

Independent CSV and XLSX structures were persisted in disposable PostgreSQL and executed through the real authorization, persistence, Tool Registry and Tool Executor path:

| Case | Expected | Actual |
|---|---|---|
| USD revenue grouped by region, including a negative value | North `220.25`; South `-20.25` | North `220.25`; South `-20.25` |
| Period comparison | baseline `80`; comparison `120`; absolute change `40 USD` | `80`, `120`, `40 USD` |
| Headcount ranking from XLSX | B `20`, A `10` | B `20`, A `10` |
| Capacity / headcount percentage | `29 / 35 × 100 = 82.8571%` | `82.8571%` |
| Zero denominator | null result plus explicit limitation | null plus `zero_denominator` |
| USD/EUR derived metric | no calculation; request clarification | `unit_mismatch`, `needs_clarification` |

Repeated identical calls returned byte-equivalent structured outputs except for the outer Tool Executor call ID/latency telemetry. A dedicated regression also verifies that numeric nulls are excluded from sum/average inputs but retained in row counts, a non-zero same-unit ratio is rounded deterministically to four decimal places, and tied ranking values retain immutable source first-occurrence order with null aggregate values placed last. Calculations never read the public network or arbitrary files and expose no SQL/Python/eval surface.

## Feature Value Gate v1

Decision: **Approve as necessary infrastructure**.

- **Work reduced:** replaces manual spreadsheet filters, pivot tables, repeated formula checking and copied calculation provenance for supported operations.
- **Measurable proxies:** one authorized Revision binding per calculation; zero client-supplied authority fields; exact expected/actual agreement; repeated-result equality; zero unauthorized publications; centralized clarification for deterministic ambiguities.
- **Product connection:** directly enables Investigate; provides verified inputs for Simulate and Decide & Review; immutable parameters/source spans support future Collaborate and reproducible branches.
- **Duplication check:** reuses parser, persistence, authorization, lifecycle and Tool Registry. It does not create another Agent, SQL engine or task state machine.
- **Costs/risks:** in-process computation, bounded but not hard-sandboxed resources, no production workload isolation, and no user-facing analysis flow yet.

Engineering evidence does not prove minutes saved by real users. A later usability baseline must compare supported calculations with spreadsheet/manual analysis and measure correction rate, clarification turns and time-to-trusted-result.

## Verification and remaining work

- **487/487 offline tests passed**.
- **179/179 disposable PostgreSQL tests passed**, including eight PV2-04c database cases.
- **20/20 focused PV2-04c offline/database tests passed**.
- No migration was added or applied to the existing application database. Feature flag `DATASET_ANALYSIS_TOOLS_ENABLED` remains false.

PV2-04d must bind validated product requests to these tools through a server-owned Planner/operation mapping and Durable Runtime without permitting free-form SQL, Python or tool selection. PV2-04e owns Chart Spec and report delivery. Full-value browser preview, public/customer upload, arbitrary business questions, prediction and production deployment remain unavailable.
