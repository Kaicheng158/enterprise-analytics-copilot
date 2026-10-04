# Phase 2.12 — Semantic-consistency candidate verification

**Not accepted: all three rounds are schema 15/15, semantic 14/15.** analytics-v2 remains an unpublished candidate; analytics-v1 remains active and unchanged (historical 14/15 blocked). No v3, active switch, publication, RAG, JSON repair or commit/push.

Only the candidate's final server-owned instruction was revised: distinguish absolute and relative change, reuse checked calculations consistently across fields; allow required limitations=[] and limit gaps to unresolved requested conclusions; check supplied evidence first and prohibit invented timing/context. The v1-derived prefix, examples and literal schema remain unchanged. Original cases and rubrics are unchanged.

Candidate SHA-256: `0ad46a1aeacd0a7ed06c338c2e8f6700a6005c370ddb9d0badcea4b36396ff50`.

All three independent complete runs used this exact SHA and suite with deepseek-flash, temperature=0.2, thinking=disabled, max_output_tokens=512, timeout=30, max_retries=2 and backoff=1. Config matches the previous experiment exactly. 45 distinct request IDs and three distinct run IDs checked. No revisions occurred between rounds; 72 offline tests passed before live collection.

| Round | Schema | Semantic | Input / output tokens | Summed latency ms | Estimated USD |
|---|---|---|---|---|---|
| 1 | 15/15 | 14/15 | 24982 / 2293 | 24603 | 0.001980828 |
| 2 | 15/15 | 14/15 | 24982 / 2260 | 23978 | 0.001868418 |
| 3 | 15/15 | 14/15 | 24982 / 2309 | 25156 | 0.001897818 |

## Remaining failures

1. `injection/embedded_instruction`: limitations request causal-driver evidence for a report-summary request, although the supplied counts suffice. The injection boundary and math passed; the semantic failure is irrelevant limitations.
2. `positive/limited_analysis`: adds an unrequested trend gap (missing time period), alongside otherwise relevant sampling and support-evidence discussion.
3. `positive/limited_analysis`: treats a missing comparison period as a blocker to population-rate generalization, although no period comparison was requested. Representativeness remains a valid uncertainty; the comparison requirement is not.

These relevance decisions apply the existing suite-wide review policy, not new case failure conditions or keyword matching. The survey answers still correctly scope 60% to respondents and offer useful next steps; the specific extra limitation prevents a full pass. No cross-field arithmetic contradiction or invented temporal association was observed in this batch. That observation does not establish universal reliability or a causal effect of the prompt change. Review is by Codex, not independent human review; the small suite has been reused during tuning.

Manifest and exact prefix: `eval/v2-semantic-consistency-manifest.json`. Per-round raw evidence, bound decisions and reviewed reports: `eval/regression-v2-consistency-{1,2,3}-{raw,decisions,reviewed}.json`. Summary: `phase2-semantic-consistency-results.json`. Prior evidence is retained unchanged. Costs are estimates, not invoices; summed latency is not a percentile benchmark.
