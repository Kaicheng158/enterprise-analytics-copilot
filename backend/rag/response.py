"""Separate RAG outer contract; AnalyticsAnswer and /chat are unchanged."""
from typing import Literal
from pydantic import BaseModel, ConfigDict
from backend.output import AnalyticsAnswer


class Citation(BaseModel):
    model_config=ConfigDict(strict=True,extra="forbid")
    field: Literal["summary","facts","interpretation","limitations"]
    index: int
    source_label: str


class RagResponse(BaseModel):
    model_config=ConfigDict(strict=True,extra="forbid")
    contract_version: Literal["rag-response-v1"]
    answer: AnalyticsAnswer
    citations: list[Citation]
    # Metadata comes ONLY from the context builder, never model-generated JSON.
    sources: list[dict]
    generation: dict
    token_budget: dict
    context_sha256: str
