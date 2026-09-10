# Architecture

Browser -> local Uvicorn/FastAPI (127.0.0.1:8765).

- GET /health returns {"status":"ok"} without querying PostgreSQL.
- GET /health/db uses backend/database.py and Psycopg to execute SELECT 1.
- PostgreSQL 17 runs in Docker Compose and is bound to 127.0.0.1:5432.
- Data persists in a Docker named volume.
- Credentials are loaded from the ignored local .env file.
- Connections and queries have five-second timeouts. Database errors return a generic HTTP 503 response.

FastAPI is not containerized. No LLM, RAG or Agent functionality is implemented.
