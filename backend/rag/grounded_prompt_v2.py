"""Unpublished independent candidate; base release remains unchanged."""
import hashlib
from backend.rag import grounded_prompt as base
from backend.rag.context import serialize
VERSION = "rag-grounded-v2"
LIMITATION_RELEVANCE = """Before including a limitation, identify how the missing information or uncertainty actually affects the user's current requested result, calculation, explanation or action. Omit it if the current request can be adequately completed without it: absence alone is not a reason to list a limitation. Do not introduce an unrequested analysis to make a missing detail seem necessary. The limitations field may be an empty array; never fill it just to populate the field. Preserve uncertainty or insufficient-evidence limitations when they genuinely constrain the requested answer, including unresolved causes or conflicting evidence."""
PREFIX = (*base.PREFIX[:-1], (base.PREFIX[-1][0], base.PREFIX[-1][1] + "\n" + LIMITATION_RELEVANCE))
SHA256 = "8d23068130bfe7ed4916fb4ce2a54cd374f038f16b677239159142a73c342889"


def messages(query, context):
    base.messages("", "") # Verify retained immutable base before deriving candidate.
    if hashlib.sha256(serialize(PREFIX).encode()).hexdigest() != SHA256:
        raise ValueError("RAG candidate checksum mismatch")
    return [*({"role":r,"content":c} for r,c in PREFIX),
            {"role":"user","content":serialize({"query":query,"evidence":context})}]
