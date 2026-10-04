"""Internal draft contracts, not API schemas or database migrations.

These immutable records carry data, not authorization or validation guarantees.
Future adapters must validate inputs and enforce server-derived access scope.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Document:
    document_id: str
    revision: str
    tenant_id: str
    title: str
    source_uri: str
    content_sha256: str
    text: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    document_id: str
    document_revision: str
    tenant_id: str
    ordinal: int
    text: str
    locator: str  # Source page/section or text offset, not a model-invented citation.
    chunker_version: str
    content_sha256: str
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class EmbeddingProfile:
    provider: str
    model: str
    revision: str
    dimensions: int
    # Profile identity must also include normalization and query/document modes.
    preprocessing_version: str


@dataclass(frozen=True)
class AccessScope:
    """Future trusted auth layer supplies this; never accept it from /chat JSON."""
    tenant_id: str
    allowed_document_ids: tuple[str, ...]  # Empty means no access, never all.


@dataclass(frozen=True)
class RetrievalHit:
    chunk: Chunk
    distance: float  # Ranking signal, not factual confidence.
    distance_metric: str


@dataclass(frozen=True)
class ContextBlock:
    """Untrusted source text plus provenance; never a system/developer message."""
    chunk_id: str
    document_id: str
    document_revision: str
    locator: str
    text: str
