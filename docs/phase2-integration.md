# Phase 2.11 — Integration verification

Integration checks passed. Known semantic regression remains **analytics-v1: 14/15, release gate blocked**. This task neither changes the published prompt nor attempts to clear that gate. A successful integration smoke call is not a new semantic-regression result.

## Checks

| Area | Evidence |
|---|---|
| Server-owned active release | API uses the real provider factory; outbound messages exactly equal the active release with system/contract/frozen schema, two example pairs, then final user. |
| Prompt identity | analytics-v1 and its SHA-256 match in HTTP response, correlated container attempt/request logs, new eval-collector tests, and the unchanged historical regression artifact. |
| Structured output | Adapter requests json_object; response parses as AnalyticsAnswer before returning. Invalid structured content fails with sanitized 502; OpenAPI and actual response fields match. |
| Retry / timeout / errors | Integrated ASGI tests exercise temporary 503 then success, timeout 504 without retry, balance 503 without retry, and invalid output 502 without repair/retry. Existing broader tests retain coverage of retry exhaustion and other error classes. |
| Telemetry | Response and request log agree on provider/model, version/hash, tokens/cache, latency, retries, cost and cost completeness. Synthetic response is correlated by request_id. Error logs are sanitized; no prompt, answer or key is logged. |
| Client authority | system_message, prompt_version, examples, output_contract and response_format all return HTTP 422. Offline tests verify no upstream call. |
| Docker reproduction | Compose build and force-recreation of API/PostgreSQL succeeded without deleting the database volume. Both services healthy; /health, /health/db and /docs returned 200. |
| API documentation | Live OpenAPI exposes the four required AnalyticsAnswer fields and prompt metadata; successful HTTP response validates with ChatResult and has the documented properties. |

## Recorded live smoke

One explicitly synthetic toy-count request through Docker /chat returned HTTP 200. Input 1123 tokens, output 127, total 1250; cache hit 896, miss 227. Latency 1797 ms; estimated USD 0.000112938; two correlated records (attempt and request). See phase2-integration-evidence.json for the actual response and safe metadata. Estimates are not billed amounts and one latency sample is not a benchmark.

66 offline tests passed, including three integrated factory/route/adapter tests in tests/test_phase2_integration.py. They mock only the external provider transport (plus environment loading), so they are distinct from the one real Docker/DeepSeek smoke call. Error scenarios are simulated; no deliberate paid-provider authentication/balance failures were induced.

Reproduce container setup with `docker compose up -d --build --force-recreate --wait`; run offline checks with `analytics-agent-env/bin/python -m unittest discover -s tests -q`. Live smoke requests incur provider usage. Existing Docker dependency layers may be reused; this verifies rebuild/recreation on the current machine, not an empty-cache build on a new host. No new dependencies, endpoints or production behavior were added.

The published analytics-v1 source and original regression evidence are unchanged. No semantic gate reset, prompt retuning, Phase 2.12, RAG, Tool Calling or Agent work was performed.
