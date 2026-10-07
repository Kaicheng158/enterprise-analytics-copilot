"""Independent unpublished v3 candidate; v1/v2 remain immutable."""
import hashlib
from backend.rag import grounded_prompt_v2 as base
from backend.rag.context import serialize
VERSION = "rag-grounded-v3"
DELIVERABLE_COMPLETION = """Before returning, ensure every deliverable explicitly requested by the user has an identifiable corresponding output. Keeping limitations relevant must not omit a requested next step, action, recommendation, calculation, explanation or comparison. If evidence cannot support a further business conclusion but a next step is requested, propose a relevant evidence-gathering, checking or validation action as a way to investigate, not as proof of an unverified cause or conclusion. Describing missing information alone is not an actionable next step. No fixed wording or unique action is required; do not invent unsupported conclusions to fill a deliverable."""
PREFIX = (*base.PREFIX[:-1], (base.PREFIX[-1][0], base.PREFIX[-1][1] + "\n" + DELIVERABLE_COMPLETION))
SHA256 = "f98b847fce3139b0ad0e70dbab01737a030220f737f03c34aaf8a3049db308ce"


def messages(query, context):
    base.messages("", "")
    if hashlib.sha256(serialize(PREFIX).encode()).hexdigest() != SHA256:
        raise ValueError("RAG candidate checksum mismatch")
    return [*({"role":r,"content":c} for r,c in PREFIX),
            {"role":"user","content":serialize({"query":query,"evidence":context})}]
