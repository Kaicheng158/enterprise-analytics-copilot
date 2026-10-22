# Phase 5.6b durable Planner live acceptance

## Verdict

The frozen `durable-planner-release-gate-v1` passed two independent three-case rounds. All six cases passed every applicable hard invariant, had zero core blockers, met the per-case threshold and produced 100% normalized round quality. There were no retries, safety stops or evidence-integrity failures.

- Provider calls: 6 of 6 allowed.
- Tokens: 5,506 input, 1,858 output, 7,364 total.
- Provider latency: 11,672 ms total.
- Estimated cost: USD 0.0038814 under the USD 0.02 hard cap.
- Feature flags after execution: all false.
- Public Planner/Agent route: absent.

## Frozen identities

- Suite SHA-256: `c059028e3b67924338bcb27ab5cf5c8f1bf7c32977ab63ab217c2d98c061df11`.
- Gate SHA-256: `a2355516e49e248e75915d3f7b94c8bf13df4cf19fee1ec592373c872fa1a692`.
- Live-plan SHA-256: `425a2613b798ab4add2894aea06a91bfc7d9c4848249422cb2f3a5fd14b0d3a7`.
- Planner prompt SHA-256: `23a1003075c87466b81525f3981a7aca75d97e64c2d1fbd349d5d40f6c39882b`.
- Plan schema SHA-256: `8ca0bc1a0a28bc75597bdd77ab56d3878260cbc8c82206c75d2a4cb9f87e5660`.

The freeze verifier recomputed every protected file and identity after execution. Six unique provider request IDs and six unique durable execution IDs were recorded. Every returned Plan parsed through the strict schema and its canonical SHA matched the persisted `plan_sha256`.

## Round results

| Round | Cases | Hard invariants | Core blockers | Quality | Tokens | Provider latency | Estimated cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| `live-v1-001` | 3/3 | 100% | 0 | 100% | 3,665 | 5,959 ms | USD 0.0019203 |
| `live-v1-002` | 3/3 | 100% | 0 | 100% | 3,699 | 5,713 ms | USD 0.0019611 |

Both rounds returned the expected synthetic results: all regions Q1→Q2 USD 400.00→600.00 (+50.00%), north Q1→Q2 USD 200.00→300.00 (+50.00%), and south Q2→Q1 USD 300.00→200.00 (-33.33%). Every accepted Plan used exactly two authorized `get_postgres_sales_summary` steps plus one local `compare_sales_growth` step. Duplicate submission reused the durable execution without a second provider request.

## Evidence integrity

- `preflight-v1-001.json`: `defafa9ac544e45a8f3a60cd3e1d6bd324f54ab28d19462c99ca7a20582851b1`.
- `live-v1-001.json`: `73e2d22a13fae7a9189024b4bb4308888f97ec60b73930329b8ede0346ab1539`.
- `live-v1-002.json`: `4181e41870a9f7e5888849f3846a4f434ff30e23c484e9b07a3ee52e9b01c75f`.
- `live-summary-v1.json`: `d88f34c2b0db98d93c7936546c1e89df43dc094ad1b399026ca78da91ef67d83`.
- `live-trace-supplement-v1.json`: `3530bbb1d1bd453896adc74f6023600404fdf7089ac012de78744bf7fa1bb1a3`.

The original paid-run files contain the actual model Plans, provider usage, latency, cost, final result and in-run Receipt/checkpoint consistency verdicts. The disposable round databases were dropped as designed, and the original files did not contain row-level Checkpoint and Receipt bodies. `live-trace-supplement-v1.json` is therefore explicitly marked as a **zero-paid deterministic replay of the exact frozen live Plans**. It binds each replay to the original evidence SHA and source Plan SHA, but does not claim its execution IDs or timestamps came from the original run. All six replay results and Plan SHAs match their source live records; each replay has seven Checkpoints and three successful Receipts.

Configured secrets, authorization headers, database credentials, local absolute paths and private diagnostics were absent from all acceptance evidence. Estimated cost is derived from provider-reported token usage and the versioned local rate table; it is not a billing statement.

## Limits

Two rounds over three synthetic read-only cases are evidence for this bounded local path only. They do not establish production reliability, broader planning quality or safety for consequential tools. Production still requires identity/RBAC, tenant isolation, a least-privilege database role, a Worker supervisor, cancellation and shutdown handling, durable provider telemetry, operator reconciliation and human approval policy. Unknown external effects still cannot be treated as exactly once.
