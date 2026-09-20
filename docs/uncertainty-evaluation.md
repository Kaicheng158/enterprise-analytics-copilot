# Phase 2.6 — Hallucination / uncertainty evaluation

Scope: six synthetic, provider-independent cases in `eval/uncertainty_cases.json`. Each has an explicit expected behavior and failure condition. No real business data, prompt injection, RAG, tools or agent execution is involved. This is a small prompt check, not an evaluation framework.

Run from the repository with `analytics-agent-env/bin/python -m eval.run_uncertainty --output eval/new-run.json`. This makes six real calls using the configured provider and may incur charges. Existing artifacts are never overwritten. The runner leaves successful calls pending semantic review; valid JSON is not an automatic semantic pass. Review each answer against both rubric fields and record a verdict and rationale. Provider errors are execution errors, not hallucination passes; unavailable usage/cost remains null, never zero.

## Method and outcome

Codex reviewed all answer fields against the prewritten rubrics; this is not an independent human assessment or an LLM-as-judge service. Baseline: 5 pass, 1 fail. The missing-metric answer copied churn-example content, failing to request active-customer inputs. The only prompt change adds a general requirement to ground every field and missing-data request in the final question, without borrowing a demonstration metric/premise. No case-specific answers or numeric confidence scores were added.

Final: 6 pass, 0 fail. Both raw runs retain answers, prompt/case SHA-256 fingerprints, model, verdict/rationale, token usage, latency, retries and estimated cost. Minor baseline wording concerns are recorded rather than hidden. One sample per case per revision does not establish stability, causal proof of improvement, or a general hallucination pass rate. No automated semantic pass threshold is claimed.

| Run / case | Verdict | Total tokens | Latency ms | Estimated USD |
|---|---|---:|---:|---:|
| baseline / unsupported_cause | pass | 1101 | 1888 | 0.00024615 |
| baseline / unsupported_premise | pass | 1009 | 1326 | 0.000080754 |
| baseline / false_premise | pass | 1066 | 1494 | 0.000113154 |
| baseline / supplied_hypothesis | pass | 1094 | 1523 | 0.000121854 |
| baseline / missing_metric | fail | 1025 | 1119 | 0.000085854 |
| baseline / unavailable_tools | pass | 1026 | 1658 | 0.000086904 |
| **baseline total** | | **6321** | **9008** | **0.000734670** |
| final / unsupported_cause | pass | 1139 | 2068 | 0.0002406 |
| final / unsupported_premise | pass | 1095 | 1690 | 0.000104004 |
| final / false_premise | pass | 1130 | 1402 | 0.000123204 |
| final / supplied_hypothesis | pass | 1198 | 1612 | 0.000155904 |
| final / missing_metric | pass | 1116 | 1297 | 0.000112104 |
| final / unavailable_tools | pass | 1111 | 1322 | 0.000109554 |
| **final total** | | **6789** | **9391** | **0.000845370** |

Latency totals sum sequential provider-request times, not benchmark averages. All calls used deepseek-flash; pricing snapshot and cache counters are recorded per call. Costs are estimates, not billed amounts. No new dependency was added.

All 44 automated tests passed. Tests cover case/rubric completeness, result metadata, pending review semantics, error sanitization and continuation, alongside the full application regression suite. Stop before Phase 2.7.
