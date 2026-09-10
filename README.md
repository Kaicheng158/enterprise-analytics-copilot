# Enterprise Analytics Copilot

Phase 0: FastAPI and PostgreSQL running together in Docker Compose.

## Start

Start Docker Desktop. From the repository root, create a local `.env` with your own password:

```text
POSTGRES_USER=analytics
POSTGRES_DB=analytics
POSTGRES_PASSWORD=<your-local-password>
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

The existing local `.env` can be reused. Never commit it. Credentials initialize a new database volume; editing them does not change existing database credentials.

```sh
docker compose up -d --build --wait
```

The API waits for PostgreSQL to be healthy. Ensure port 8765 is not occupied by a separately running Uvicorn process.

## Verify

```sh
curl -i http://127.0.0.1:8765/health
curl -i http://127.0.0.1:8765/health/db
docker compose ps
```

Both requests should return HTTP 200:

- `/health`: `{"status":"ok"}`
- `/health/db`: `{"status":"ok","database":"connected","result":1}` (executes SELECT 1).

Database errors return HTTP 503. Both published ports bind to localhost only.

## Stop and restart

```sh
docker compose down
docker compose up -d --wait
```

The named database volume is preserved. Do not add `--volumes` unless intentionally deleting database data.

## Development

The API image installs pinned dependencies from requirements.txt and runs as a non-root user. Only backend Python files and requirements are included in its build context; .env and the local virtual environment are excluded.

For optional host development, use Python 3.12 with analytics-agent-env and install requirements.txt there. Stop the Compose API first (`docker compose stop api`), then run `python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765`. The host uses POSTGRES_HOST/PORT from .env; Compose overrides them with postgres:5432 inside the API container.

Documentation lives in docs/. LLM, RAG and Agent features are not implemented. The database role is for isolated local development; production permissions are a later milestone.
