# Phase 4.5 Evaluator v3 guarded recovery acceptance

Status: PASS. This run does not overwrite or re-score the historical v3 FAIL.

## Frozen identities

- Suite SHA-256: `78035ed0e09499eb2dbb2fbe9c52cac56476db414c3446b44927891980b3df3f`
- Gate SHA-256: `ce548ce798b5c2f3ad9fc2e14c483b2adc4d4c91b304375c4970dbf0ee49d497`
- Prompt SHA-256: `af1574d65979199045b6ab2cb8267fae23a3bb1887e48d1a53e51ee9ec682dfb`
- Recovery plan SHA-256: `6933d17f234b566d6de7b0e09dbb4e1a8d450fa122f19c37774ba8f785acab10`

The original v3 evaluator, suite, rubric, gate, prompt, DeepSeek Flash config,
source binding, and deterministic controls were reused unchanged.

## Results

| Run | Cases | Verdict | Quality | Tokens | Latency | Estimated cost |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| `live-v3-recovery-001` | 9/9 | PASS | 100% | 6,194 | 15,004 ms | USD 0.001044744 |
| `live-v3-recovery-002` | 9/9 | PASS | 100% | 6,207 | 15,490 ms | USD 0.001051644 |
| `live-v3-recovery-003` | 9/9 | PASS | 100% | 6,199 | 14,406 ms | USD 0.001039194 |

Total: 27/27 cases PASS, 18,600 tokens, 44,900 ms, USD 0.003135582,
39 provider interactions, zero retries, and no stop condition.

The release gate received three complete live runs. Every run had all hard
invariants pass, zero core blockers, no below-threshold cases, no repeated
cross-run failures, and normalized quality 1.0. Final gate verdict: PASS.

## Infrastructure guard

The two PostgreSQL-required cases in each round used a database runtime guard
before both the initial and final provider interactions. All 12 checks passed.
Each check verified the Agent-side connection, `SELECT 1`, the sales relation,
the expected synthetic dataset, and the real PostgreSQL sales tool.

If a check had failed, the corresponding provider interaction and all remaining
paid cases would have been stopped with safe infrastructure evidence.

## Safety and preservation

- Cumulative cost stayed below the USD 0.02 limit.
- No API key, authorization header, or secret field appears in the evidence.
- Historical v3 FAIL evidence and frozen artifacts passed hash verification.
- `TOOL_CALLING_ENABLED` and `TOOL_CALLING_LIVE_REGRESSION_ENABLED` are false.
- No public Tool API was enabled.
- Final tests: 285 offline and 44 database tests passed.

Evidence is stored under `eval/tools/v3/live-v3-recovery-001` through `003`.
The aggregate result is `eval/tools/v3/live-regression-recovery-summary-v3.json`.
