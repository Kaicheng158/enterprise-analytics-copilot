# Phase 2.12 — Fixed-config stability verification

Result: **blocked; analytics-v2 remains unpublished and analytics-v1 remains active**. Historical v1 is immutable, 14/15 blocked. No v3, RAG, repair, relaxed schema, changed rubric or case.

## Frozen experiment

Three complete, independent 15-case DeepSeek runs used the same candidate SHA, original suite and validated runtime config; 45 distinct request IDs and three distinct run IDs were verified. No prompt/config adjustment occurred between rounds.

- Candidate SHA-256: `1f44fe7568189677d44d9cdb512e2b608125d341e510fea7cebc2144d8644072`
- Model: `deepseek-flash`; thinking: `disabled`; temperature: **0.2**; max_output_tokens: **512**.
- Timeout: 30 seconds; max_retries: 2; backoff: 1 second.
- Manifest/prefix: `eval/v2-fixed-temperature-manifest.json`.
- Evidence: `eval/regression-v2-fixed-{1,2,3}-{raw,decisions,reviewed}.json`.

The candidate adds generic causal-evidence requests and relevance/availability checks to its server-owned boundary. It contains no case-specific values or answers. The historical r1–r13 attempts remain retained and are not regraded.

| Round | Semantic | Schema | Input / output tokens | Summed latency ms | Estimated USD |
|---|---|---|---|---|---|
| 1 | 13/15 | 15/15 | 22252 / 2260 | 24464 | 0.001909032 |
| 2 | 13/15 | 15/15 | 22252 / 2277 | 24261 | 0.00188160 |
| 3 | 11/15 | 15/15 | 22252 / 2309 | 23290 | 0.00190080 |

All three rounds misstate the metric-change percentage in summary (20%) despite correct 25% calculations in facts. All three also introduce irrelevant missing-data requirements in the embedded-report case. Round 3 additionally invents temporal redesign evidence for the supplied hypothesis, and re-requests comparable weekly information in the Chinese case. Verdicts assess every field using the unchanged case expectations and suite-wide truthfulness/relevance policy. JSON validity is separate from semantic pass.

72 offline tests passed. All 45 live outputs were schema-valid; therefore the new failure categories were verified with offline simulated responses, not observed live in this batch. Low temperature did not eliminate semantic errors. These are small-sample observations after earlier tuning, not a causal estimate of temperature's effect or a reliability guarantee. Reviews are Codex review, not independent human review. Costs are estimates, not billing records; summed latency is not a per-call percentile.

## Safe output diagnostics

`llm_invalid_output` keeps the sanitized public error. Internal logs/eval evidence carry `output_diagnostic` with stage, fixed reason code, safe finish_reason enum, content length, and where applicable parser position or a list of schema issue categories. No content, user field names, Pydantic input/errors text, exception text, headers, or key is retained. Raw answers are deliberately not included in failure diagnostics; successful synthetic eval answers retain the existing evidence behavior.

Categories: empty_content; content_type_error; non_json (non-JSON-looking text rejected by parser); json_parse_error (JSON-looking malformed text); invalid_json_structure (duplicate keys/nonstandard constants/depth failure); schema_field_missing; type_error; extra_field; truncation (finish_reason=length); incomplete_output (other non-stop completion). Multiple schema categories are retained. Validated provider usage and estimated cost are preserved even when output fails; absent/invalid usage is not guessed. Invalid output is not retried or repaired.

`LLM_TEMPERATURE` is centrally validated in [0,2], finite, defaults to 0.2, and is explicitly sent upstream and recorded in generation metadata. Compose forwards it. Existing running containers have not been recreated because v2 publication prerequisites failed; rebuild is still required to deploy these local application changes.

To reproduce a new run without overwriting evidence, set `LLM_TEMPERATURE=0.2 LLM_MODEL=deepseek-flash LLM_MAX_OUTPUT_TOKENS=512 LLM_TIMEOUT_SECONDS=30 LLM_MAX_RETRIES=2 LLM_BACKOFF_SECONDS=1` and use the existing `python -m eval.run_regression run --candidate analytics-v2 --output <new-path>` command. Inspect the saved runtime config before comparing. Thinking is fixed disabled. Pending semantic review intentionally exits nonzero; use evidence-bound review decisions for final verdicts.

No v2 publication, active switch or post-switch Docker acceptance was performed. The final release commit/push remains pending the acceptance gate; engineering changes and all evidence remain local.
