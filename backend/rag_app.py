"""Local RAG deployment entrypoint; frozen chat application source unchanged."""
from backend.main import app
from backend.rag.api import router
app.include_router(router)
