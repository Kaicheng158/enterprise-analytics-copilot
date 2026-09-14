# Phase 1 acceptance — 2026-09-14

Status: passed for the scoped single-provider local application.

| Check | Evidence |
|---|---|
| Normal /chat | Real DeepSeek request after Compose recreation: HTTP 200, OK, 17 tokens |
| Custom system message | Real request: HTTP 200, OK, 12 tokens; tests confirm separate system/user roles |
| Input validation | HTTP/ASGI tests cover missing, blank, wrong type, malformed JSON, unknown and ambiguous fields, and length limits |
| Provider errors | Offline tests cover authentication, balance, access, rate limit, malformed payload and configuration errors; sanitized responses |
| Timeout/retry | Offline tests verify timeout propagation, no ambiguous network retries, deterministic-error exclusions, bounded attempts and exponential backoff |
| Logging and cost | Correlated attempt/request records verified; cache-aware decimal prices; failed-attempt cost remains unknown |
| Environment safety | .env ignored and untracked; configured credentials absent from logs; .env and venv absent from image; non-root API |
| Docker reproducibility | docker compose down followed by up -d --build --wait succeeded with existing named volume retained; both containers healthy |
| Regression | 31 unittest tests pass; pip check passes; /health and /health/db return HTTP 200 |

Fix during closeout: token usage now requires strict nonnegative integers and consistent total/cache/reasoning counts. Invalid upstream counts return 502 rather than producing misleading cost estimates.

Live samples (not benchmarks): normal call 1111 ms, estimated USD 0.000006; custom system call 1096 ms, estimated USD 0.0000045. Both used deepseek-flash, zero retries, and peak pricing snapshot deepseek-2026-09-13.

## Scope and limits

- One provider, single-turn text, no tools or RAG.
- Timeout is per network operation, not a strict end-to-end deadline.
- Cost is an estimate for reported usage; retries may have unknown extra charges. Logs are not a durable billing ledger.
- Docker recreation was tested on this Mac with the existing local configuration and database volume; this is not a clean-machine or load test.
- No Phase 2 work was started.
