# Roadmap

## Overall stages

0. Engineering foundation — complete: FastAPI + PostgreSQL in Compose.
1. LLM API and prompts — complete, DeepSeek only.
2. Prompt Engineering — tasks 2.1–2.2 complete; remaining tasks below.
3. Safe SQL and tool calling — not started.
4. Analysis tools and controlled agent — not started.
5. LangGraph workflows — not started.
6. Evaluation — not started.
7. Deployment — not started.
8. Enterprise permissions, security and human review — not started.

## Phase 1 engineering checklist

Completed baseline: real DeepSeek API integration, provider abstraction, POST /chat, request/response schemas, reported token usage, basic successful-call latency logging, model name from environment.

1. [x] Error handling: stable error codes; sanitized authentication/balance/rate-limit/upstream errors; wrapped timeouts, broken connections and malformed responses; API error-contract regression tests.
2. [x] Timeout/retry: configurable timeouts; bounded attempts and backoff; explicit retryable errors; avoid retries for authentication/balance/invalid requests; tests of attempt counts and limits. Completed: validated timeout/retry configuration, bounded HTTP retries with exponential backoff/jitter; ambiguous network failures are not retried.
3. [x] Usage/latency/cost logging: request correlation, success/failure records, per-attempt versus total timing, versioned model pricing and currency for estimates, cache accounting, unknown usage/cost handling. Estimates must not be presented as billed amounts; no billing ledger or quota implementation.
4. [x] Model config: centralized validated DeepSeek model, output limits and generation settings; update env example and Compose configuration. Completed: central validated config/catalog, price snapshot, environment precedence and one registered adapter.
5. [x] Basic system/user messages: configurable system instruction and validated user message; no conversation memory or tools.
6. [x] Phase 1 closeout: full regression suite, bounded real-call verification, configuration/startup checks, updated README and completion criteria.

Work rule: execute one task, run relevant tests, update documentation, commit and push, then stop. Phase 1 accepted on 2026-09-14. All six engineering tasks complete. Phase 2 now follows the user-defined Prompt Engineering roadmap.

## Future direction (not implemented)

Multiple providers/models and quality/latency/cost comparisons; platform-managed keys, BYOK, durable usage tracking, user quota and billing. Only the provider boundary is reserved today. Do not begin RAG, Tool Calling, Agent or LangGraph during Phase 1.

## Phase 2 — Prompt Engineering (supersedes the former RAG phase label)

- [x] 2.1 Prompt architecture: provider-independent message assembly in backend/prompts.py.
- [x] 2.2 Server-owned System Prompt v1: role, goal, data boundaries and insufficient-information instruction; public overrides rejected.
- [ ] 2.3 Output contract
- [ ] 2.4 Structured output / JSON
- [ ] 2.5 Few-shot examples
- [ ] 2.6 Hallucination / uncertainty handling (dedicated implementation and evaluation)
- [ ] 2.7 Prompt injection basic boundaries
- [ ] 2.8 Token / context efficiency
- [ ] 2.9 Prompt versioning (registry/selection not implemented)
- [ ] 2.10 Prompt regression testing (dedicated suite)
- [ ] 2.11 Phase 2 integration
- [ ] 2.12 Phase 2 final verification

Stop after 2.2. RAG is deferred beyond this phase; its future numbering is not yet assigned. Later SQL/Tool Calling/Agent/LangGraph stages remain unstarted.
