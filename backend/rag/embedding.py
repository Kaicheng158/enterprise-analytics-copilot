"""Independent of chat providers; no SDK, model selection or network calls."""
from typing import Protocol
from .models import EmbeddingProfile

Vector = tuple[float, ...]


class EmbeddingProvider(Protocol):
    @property
    def profile(self) -> EmbeddingProfile: ...

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[Vector, ...]:
        """One finite, dimension-validated vector per text, preserving order."""
        ...

    def embed_query(self, text: str) -> Vector:
        """Use the query mode compatible with this profile's document vectors."""
        ...
