# Architecture

Browser -> 127.0.0.1:8765 -> API container port 8000 (Uvicorn/FastAPI) -> postgres:5432 (PostgreSQL container).

- GET /health checks API responsiveness.
- GET /health/db executes SELECT 1 using Psycopg, with bounded connection/query timeouts.
- Compose starts the API after PostgreSQL passes its healthcheck; API healthcheck queries /health/db.
- PostgreSQL data persists in the existing named volume.
- Credentials are injected at runtime from the ignored local .env; no secrets are copied into the image.
- API runs as a non-root user. Only localhost ports are published.

Phase 1 chat flow: POST /chat -> validated ChatRequest -> LLMProvider protocol -> DeepSeekProvider -> official DeepSeek Chat Completions API -> normalized ChatResult.

Provider selection is wired by get_provider; routes do not construct provider-specific HTTP requests. Only DeepSeek is supported today. The API key is injected at runtime; health endpoints do not require a configured LLM key.

Future direction (not implemented): provider/model switching and quality/latency/cost comparisons; platform-managed keys or BYOK; usage persistence, user quotas and billing. A future key resolver belongs at provider construction, rather than the business route. Current usage counters are provider-reported counts; versioned price configuration provides estimates, not billed charges.

No RAG, Tool Calling, Agent or LangGraph is implemented.

The provider emits correlated attempt and request-summary logs. A bounded HTTP retry loop surrounds each call. Request latency includes backoff; successful-attempt token usage and cost remain separate from unknown failed-attempt charges.

Central configuration: backend/config.py loads validated settings and the provider/price catalog. The provider factory selects the sole registered DeepSeek adapter. ChatRequest separates system/user content; the adapter serializes them as distinct role messages.

Phase 2.1–2.2 supersedes the Phase 1 system-message interface: public requests carry only user content; backend/prompts.py assembles the fixed system instruction and user message.
