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
- Return reported usage and measure provider latency; do not implement billing or monetary estimates yet.
- Store DeepSeek key in ignored .env and runtime container environment; .env.example contains placeholders only.
