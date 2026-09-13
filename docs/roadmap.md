# Roadmap

## Overall stages

0. Engineering foundation — complete: FastAPI + PostgreSQL in Compose.
1. LLM API and prompts — in progress, DeepSeek only.
2. Business knowledge RAG — not started.
3. Safe SQL and tool calling — not started.
4. Analysis tools and controlled agent — not started.
5. LangGraph workflows — not started.
6. Evaluation — not started.
7. Deployment — not started.
8. Enterprise permissions, security and human review — not started.

## Phase 1 engineering checklist

Completed baseline: real DeepSeek API integration, provider abstraction, POST /chat, request/response schemas, reported token usage, basic successful-call latency logging, model name from environment.

1. [x] Error handling: stable error codes; sanitized authentication/balance/rate-limit/upstream errors; wrapped timeouts, broken connections and malformed responses; API error-contract regression tests.
2. [ ] Timeout/retry: configurable timeouts; bounded attempts and backoff; explicit retryable errors; avoid retries for authentication/balance/invalid requests; tests of attempt counts and limits. Current state: fixed 30-second per-operation timeout, no retries.
3. [ ] Usage/latency/cost logging: request correlation, success/failure records, per-attempt versus total timing, versioned model pricing and currency for estimates, cache accounting, unknown usage/cost handling. Estimates must not be presented as billed amounts; no billing ledger or quota implementation.
4. [ ] Model config: centralized validated DeepSeek model, output limits and generation settings; update env example and Compose configuration. Current state: model configurable, other values hardcoded.
5. [ ] Basic system/user messages: configurable system instruction and validated user message; no conversation memory or tools.
6. [ ] Phase 1 closeout: full regression suite, bounded real-call verification, configuration/startup checks, updated README and completion criteria.

Work rule: execute one task, run relevant tests, update documentation, commit and push, then stop. Next task is 2; it has not been started.

## Future direction (not implemented)

Multiple providers/models and quality/latency/cost comparisons; platform-managed keys, BYOK, durable usage tracking, user quota and billing. Only the provider boundary is reserved today. Do not begin RAG, Tool Calling, Agent or LangGraph during Phase 1.
