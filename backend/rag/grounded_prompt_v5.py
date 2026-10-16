"""Independent unpublished v5: conditional conclusions require supported premises."""
import hashlib
from backend.rag import grounded_prompt_v4 as base
from backend.rag.context import serialize
from backend.rag.response_semantics_draft import FIELD_RESPONSIBILITIES, ATTRIBUTED_EXAMPLES

VERSION = "rag-grounded-v5"
PREFIX = ((base.PREFIX[0][0], base.PREFIX[0][1] + '\nConditional inference boundary:\nConditional conclusions require evidence for every premise needed to make the consequence follow. Do not infer unobserved quantities, direction of change, coverage or relationships merely from the possible existence of additional entities or information. If required premises are unknown, omit the unrequested extrapolation; when explicitly requested, identify it as unresolved rather than a definite consequence.'), *base.PREFIX[1:])
SHA256 = 'd81ef6b9862fbac388a1a79c48898b68fac0fea1c307241a95f96c243581afb3'


def messages(query, context):
    base.messages("", "")
    if hashlib.sha256(serialize(PREFIX).encode()).hexdigest() != SHA256:
        raise ValueError("RAG candidate checksum mismatch")
    return [*({"role":role,"content":content} for role,content in PREFIX),
            {"role":"user","content":serialize({"query":query,"evidence":context})}]
