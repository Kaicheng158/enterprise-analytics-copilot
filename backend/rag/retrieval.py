"""Future read boundary; no SQL, vector store or search implementation yet."""
from typing import Protocol
from .embedding import Vector
from .models import AccessScope, EmbeddingProfile, RetrievalHit


class Retriever(Protocol):
    def search(self, query_vector: Vector, *, profile: EmbeddingProfile,
               scope: AccessScope, limit: int) -> tuple[RetrievalHit, ...]:
        """Filter access and embedding identity before ranking; fail closed."""
        ...
