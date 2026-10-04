"""Boundary for deterministic, versioned splitting; no chunking algorithm yet."""
from typing import Protocol
from .models import Chunk, Document


class Chunker(Protocol):
    def split(self, document: Document) -> tuple[Chunk, ...]:
        """Preserve document revision and source locators in every chunk."""
        ...
