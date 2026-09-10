# Decisions

- Use an isolated Python 3.12 venv named analytics-agent-env.
- Do not modify system Python or Conda environments.
- Keep dependencies minimal and pin installed versions.
- Keep documentation in README.md and docs/; disable GitHub Wiki.
- Exclude virtual environments, secrets and system artifacts from Git.
- Run only PostgreSQL in Compose at this stage; keep FastAPI on the host.
- Preserve /health semantics and use /health/db for SELECT 1 connectivity verification.
- Use Psycopg binary and python-dotenv in the project venv; keep credentials out of Git.
