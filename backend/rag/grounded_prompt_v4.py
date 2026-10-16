"""Independent unpublished v4: prepared field semantics and attributed examples."""
import hashlib
from backend.rag import grounded_prompt_v3 as base
from backend.rag.context import serialize
from backend.rag.response_semantics_draft import FIELD_RESPONSIBILITIES, ATTRIBUTED_EXAMPLES

VERSION = "rag-grounded-v4"
PREFIX = ((base.PREFIX[0][0], base.PREFIX[0][1] + "\nField responsibilities:\n" + FIELD_RESPONSIBILITIES),
          *ATTRIBUTED_EXAMPLES, base.PREFIX[-1])
SHA256 = "ba038c09f78a629fcc0a09404c1f52dec3888e53beefc3edc62efa133cbf1660"


def messages(query, context):
    base.messages("", "")
    if hashlib.sha256(serialize(PREFIX).encode()).hexdigest() != SHA256:
        raise ValueError("RAG candidate checksum mismatch")
    return [*({"role":role,"content":content} for role,content in PREFIX),
            {"role":"user","content":serialize({"query":query,"evidence":context})}]
