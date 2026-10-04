"""Future bounded evidence assembly, deliberately independent of chat prompts."""
from typing import Protocol
from .models import ContextBlock, RetrievalHit


class ContextBuilder(Protocol):
    def build(self, hits: tuple[RetrievalHit, ...], *, token_budget: int
              ) -> tuple[ContextBlock, ...]:
        """Deduplicate and budget evidence with provenance; never elevate authority.

        Concrete implementation must use a documented tokenizer/accounting method.
        This boundary returns data blocks, not LLM role/message dictionaries.
        """
        ...
