"""Boundary for future allowlisted source loading; no loader or upload route yet."""
from pathlib import Path
from typing import Protocol
from .models import Document


class DocumentLoader(Protocol):
    def load(self, path: Path, *, tenant_id: str) -> Document:
        """Validate source/type/size, extract text and retain provenance."""
        ...
