# Phase 2.12 — Scope/relevance candidate verification

Result: **blocked**, schema 15/15 in each round, semantic **14/15, 14/15, 15/15**. analytics-v2 remains unpublished; analytics-v1 remains active and immutable (historical 14/15 blocked). No active switch, v3, RAG, JSON repair, commit or push.

Only two replacements in the candidate's existing final system boundary narrow analysis to the explicit user task and tie each limitation to a material effect on a requested conclusion/action. The frozen v1-derived prefix, few-shot examples, literal schema, generation config and original case/rubric files remain unchanged. No case-specific data or answers were added.

Candidate SHA-256: `60fbcc4661bbdf0f53c99404501083d4ca7584259dd3d2fd2a5601d292d6dc6d`.

The three sequential, independent complete runs share this exact SHA, suite, deepseek-flash model, temperature=0.2, thinking=disabled, max_output_tokens=512, timeout=30, max_retries=2 and backoff=1. Config matches the preceding manifest exactly. Verified three distinct run IDs and 45 distinct request IDs. No edits occurred between runs. All 72 offline tests passed before real collection.

| Round | Schema | Semantic | Input / output tokens | Summed latency ms | Estimated USD |
|---|---|---|---|---|---|
| 1 | 15/15 | 14/15 | 26497 / 2155 | 24263 | 0.001955934 |
| 2 | 15/15 | 14/15 | 26497 / 2146 | 22311 | 0.001875270 |
| 3 | 15/15 | 15/15 | 26497 / 2107 | 23269 | 0.001851870 |

## Remaining failures

- Round 1, `uncertainty/supplied_hypothesis`: correctly rejects proof and separates the hypothesis from facts, but omits the evidence request required by expected_behavior. Empty limitations itself is allowed; the missing next evidence need across the entire answer is the failure. It does not trigger the case's narrower hallucinated-cause condition.
- Round 2, `positive/limited_analysis`: invents a requested next step of investigating why support is slow and treats missing ticket/case data as blocking that invented task. The user requested a useful interpretation and a next step, not specifically a causal investigation. This fails existing expected behavior/suite-wide relevance; it is not a schema failure.
- Round 3: all 15 cases pass recorded semantic review. One passing round does not satisfy three consecutive full passes.

Per-case verdicts are evidence-bound Codex semantic review against unchanged expected_behavior, failure_condition and suite-wide policy, not keyword grading or independent human review. Conditional offers for a future analytics request are distinguished from falsely asserting an actual requested task. Valid sampling uncertainty is not itself a relevance failure. Small reused-suite results do not establish universal reliability. Estimated costs are not billed amounts; latency totals are not percentile benchmarks.

Exact prefix/config: `eval/v2-scope-relevance-manifest.json`. Raw evidence, decisions and reviewed reports: `eval/regression-v2-scope-{1,2,3}-{raw,decisions,reviewed}.json`. Metrics summary: `phase2-scope-relevance-results.json`. All previous experiment evidence is retained unchanged.
