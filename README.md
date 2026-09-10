# Enterprise Analytics Copilot

A progressively developed enterprise analytics assistant.

## Current milestone

Minimal FastAPI service with GET /health. PostgreSQL, Docker and AI integrations are not implemented yet.

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
