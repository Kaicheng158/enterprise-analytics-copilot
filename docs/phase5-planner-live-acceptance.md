# Phase 5.4c — Frozen Planner live acceptance

## Frozen identity and scope

The live run used the preregistered `planner-suite-v1`, `planner-release-gate-v1`, `bounded-planner-v1` prompt and strict Plan schema without modification. The suite contains ten cases: seven deterministic safety/control cases and three live-eligible positive cases. Three independent rounds of those three cases produce the fixed maximum of nine provider calls.

- suite SHA-256: `60881b4764c4e880afcea589b4373fc1ee6cc92c4cb3ae84305907f9fadee7f8`;
- gate SHA-256: `164d0985b5e05159f1b26d1d0befad5002ccbaabecb37f54483fa861d69b40f7`;
- prompt SHA-256: `23a1003075c87466b81525f3981a7aca75d97e64c2d1fbd349d5d40f6c39882b`;
- schema SHA-256: `8ca0bc1a0a28bc75597bdd77ab56d3878260cbc8c82206c75d2a4cb9f87e5660`.

The final preflight passed frozen identity, offline/database tests, disabled flags, absent public API, database/tool health, API-key presence without disclosure, cost reserve and write-once evidence checks. Every paid request was preceded by a PostgreSQL/runtime-tool health check. DeepSeek Flash ran with thinking disabled, 800 maximum output tokens, one provider call per case and zero retries.

## Results

| Round | Cases | Hard/schema/authorization | Core blockers | Quality | Input/output tokens | Latency | Estimated cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| live-v1-001 | 3/3 PASS | 100% | 0 | 100% | 2,753 / 892 | 4,664 ms | USD 0.0018963 |
| live-v1-002 | 3/3 PASS | 100% | 0 | 100% | 2,753 / 939 | 4,879 ms | USD 0.0019527 |
| live-v1-003 | 3/3 PASS | 100% | 0 | 100% | 2,753 / 903 | 4,756 ms | USD 0.0019095 |

Total: nine calls, 8,259 input tokens, 2,734 output tokens, 10,993 total tokens, 14,299 ms provider latency and estimated cost USD 0.0057585. The USD 0.02 stop budget was not approached and no stop condition fired.

All rounds returned the same verified business outcomes: all-region Q1/Q2 USD 400/600 (+50%), north Q1/Q2 USD 200/300 (+50%), and south Q1/Q3 USD 200/0 (-100%). Plan IDs, step IDs and descriptions varied between generations, so exact Plan hashes differ. Every variant preserved the same authorized action graph, request scope and final output. Stability here means three consistent passing executions, not byte-identical generation or production reliability.

The final release gate has ten passing deterministic controls, three passing live rounds and no blockers: **PASS**. The original `gate-status-v1.json` remains the immutable preregistration state (`BLOCKED` before live rounds); the final result is recorded separately in `live-summary-v1.json`.

Final code/evidence verification passed 352 offline tests and 59 isolated database tests.

## Safety and limitations

Evidence files contain synthetic requests, generated Plans, execution results and safe telemetry. They exclude credentials, authorization headers and environment values, are written once, and refuse overwrite. All Planner flags returned to false after the one process exited. No public API was added.

This is local acceptance for one fixed synthetic sales workflow. It does not establish production identity/RBAC, distributed lease behavior, durable planner execution, human approval, broader task planning quality or production service reliability.
