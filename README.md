# Enterprise Analytics Copilot

A progressively developed enterprise analytics assistant.

## Current milestone

FastAPI runs locally. PostgreSQL runs in Docker Compose. GET /health checks the API; GET /health/db executes SELECT 1 against PostgreSQL. FastAPI is not containerized.

## Run locally

Use Python 3.12 and run these commands from the repository root:

```sh
python3.12 -m venv analytics-agent-env
source analytics-agent-env/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn backend.main:app --host 127.0.0.1 --port 8765
```

In another terminal:

```sh
curl -i http://127.0.0.1:8765/health
```

Expected: HTTP 200 and `{"status":"ok"}`. This endpoint checks API responsiveness only.
Stop the server with Ctrl+C.

Documentation lives in docs/. The virtual environment is excluded from Git.

## PostgreSQL (local development)

Start Docker Desktop first. Create a local `.env` in the repository root with these keys (choose your own password; never commit this file):

```text
POSTGRES_USER=analytics
POSTGRES_DB=analytics
POSTGRES_PASSWORD=<your-local-password>
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

```sh
docker compose up -d --wait postgres
curl -i http://127.0.0.1:8765/health/db
```

Expected HTTP 200: `{"status":"ok","database":"connected","result":1}`.
Database connection failures return HTTP 503. The database is bound to localhost only.
Credentials initialize a new database volume; changing them in .env does not change existing database credentials.

```sh
docker compose stop postgres
```

This preserves the named database volume. The local development database role is for this isolated prototype; production permissions are a future milestone.
