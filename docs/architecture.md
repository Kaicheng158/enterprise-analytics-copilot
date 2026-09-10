# Architecture

HTTP client -> Uvicorn -> FastAPI application in backend/main.py -> JSON response.

GET /health returns HTTP 200 and {"status":"ok"}. It does not test database connectivity.

frontend/, data/ and eval/ are reserved for future work. No database or AI integration is implemented.
