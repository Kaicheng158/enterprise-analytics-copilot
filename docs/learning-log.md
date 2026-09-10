# Learning log

- Confirmed the distinction between API responsiveness and database connectivity.
- Initialized project structure and isolated Python 3.12 environment.
- Implemented GET /health with FastAPI and Uvicorn; pinned dependencies.
- Port 8000 was occupied; used 8765 without disturbing the existing service.
- Verified a real HTTP request to port 8765 returned 200 and {"status":"ok"}; stopped the test server afterward.
