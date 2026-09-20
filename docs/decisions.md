# Decisions

- Use an isolated Python 3.12 venv named analytics-agent-env.
- Do not modify system Python or Conda environments.
- Keep dependencies minimal and pin installed versions.
- Keep documentation in README.md and docs/; disable GitHub Wiki.
- Exclude virtual environments, secrets and system artifacts from Git.
- Previous milestone ran PostgreSQL in Compose with FastAPI on the host. Phase 0 now runs both in Compose.
- Preserve /health semantics and use /health/db for SELECT 1 connectivity verification.
- Use Psycopg binary and python-dotenv in the project venv; keep credentials out of Git.
- Use postgres:5432 within the Compose network, regardless of host database port settings.
- Preserve the database volume and the browser API address during containerization.

- Phase 1 implements DeepSeek only, using official deepseek-flash and a minimal provider protocol.
- Use standard-library HTTP rather than adding an SDK for one endpoint.
- Return reported usage and provider latency. Phase 1 now includes versioned cost estimates; billing remains out of scope.
- Store DeepSeek key in ignored .env and runtime container environment; .env.example contains placeholders only.
- Retry only an explicit HTTP status allowlist; do not retry ambiguous network failures. Record unknown failed-attempt usage rather than claiming complete cost.
- Maintain price snapshots in backend/config.py and choose peak/off-peak tier by attempt start UTC; use decimal arithmetic.
- Centralize all runtime defaults and model/price catalog in backend/config.py; Compose forwards optional overrides without duplicating defaults.
- /chat uses separate user_message and optional system_message; preserve legacy message alias but reject ambiguous dual fields.

- Phase 2.1–2.2 supersedes public system_message support from Phase 1. Core prompt is server-owned in backend/prompts.py; no environment override.
- Phase 2 is now Prompt Engineering; RAG deferred. V1 defines basic factuality and uncertainty boundaries without implementing later dedicated tasks.
