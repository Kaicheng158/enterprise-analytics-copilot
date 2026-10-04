"""Document embedding contracts and validation; query embedding is not implemented."""
from dataclasses import dataclass
import math
from typing import Protocol
from .models import EmbeddingProfile

Vector = tuple[float, ...]


class EmbeddingError(ValueError):
    """Only fixed safe error codes, never provider bodies or source text."""


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: tuple[Vector, ...]
    profile: EmbeddingProfile
    input_tokens: int
    latency_ms: int
    retry_count: int
    estimated_cost_usd: str
    cost_complete: bool


class EmbeddingProvider(Protocol):
    @property
    def profile(self) -> EmbeddingProfile: ...

    def embed_documents(self, texts: tuple[str, ...]) -> EmbeddingBatch: ...


def validate_vectors(vectors, count, dimensions):
    if len(vectors) != count:
        raise EmbeddingError('embedding_count_mismatch')
    for vector in vectors:
        if len(vector) != dimensions:
            raise EmbeddingError('embedding_dimension_mismatch')
        if any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 3.4e38 for x in vector):
            raise EmbeddingError('embedding_invalid_number')
        if not any(abs(x) >= 1.17549435e-38 for x in vector):
            raise EmbeddingError('embedding_zero_vector')
