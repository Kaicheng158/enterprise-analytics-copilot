"""Offline preparation components only: no release/version selection or live prompt."""
from backend.output import parse_answer
from backend.rag.grounded_prompt import EXAMPLES as HISTORICAL_EXAMPLES
from backend.rag.context import serialize

FIELD_RESPONSIBILITIES = """summary: Briefly answer the current request, introducing no unsupported claims.
facts: Include only evidence-supported or explicitly user-supplied facts and reproducible calculations. Attribute each item with its current evidence source label or [user]. Never place hypotheses, proposed actions or a no-evidence status message here. When no relevant supported facts are available, facts may be [].
interpretation: Explain the facts, separate hypotheses from established facts, and include any next step, action or recommendation explicitly requested by the user. Identify a proposed evidence-gathering, checking or validation action as a suggestion to investigate, not as an action already executed or a cause/conclusion already verified. General investigative methods need not be presented as facts stated by a source.
limitations: Include only missing information or uncertainty that materially affects the current requested answer or action; absence alone is insufficient. limitations=[] is valid. Put an insufficient-evidence status in summary/limitations when appropriate, without inventing citations to irrelevant material. Describing missing inputs cannot replace an explicitly requested action in interpretation.
Complete every explicitly requested deliverable; no fixed wording or unique action is required. Existing source-label membership and attribution rules still apply to every facts item. Moving a business factual assertion to another field does not remove its evidence obligation."""


EXAMPLE_ROLE_EDITS = (
    (
        "Suggested next step: request dated prices, traffic and conversion data to investigate the proposed explanation; these alone may not establish causality.",
        "No price history or evidence linking price changes to orders was supplied."
    ),
    (
        "Suggested next step: obtain the churn definition, periods, churned and eligible customer counts for both periods to establish whether an increase occurred. Only after confirming the change, examine relevant customer or cancellation evidence to investigate causes.",
        "The churn definition, comparison measurements and relevant causal evidence are absent, so the increase, magnitude and cause cannot be established."
    ),
)


def _consistent_examples():
    """Same historical synthetic scenarios; attribution plus explicit field placement.
    User/assistant demonstration data are never sources for the actual query.
    """
    result=[]
    assistant_index=0
    for role,content in HISTORICAL_EXAMPLES:
        if role=='assistant':
            answer=parse_answer(content)
            answer.facts=[text+' [user]' for text in answer.facts]
            action,limitation=EXAMPLE_ROLE_EDITS[assistant_index]
            answer.interpretation.append(action)
            answer.limitations=[limitation]
            assistant_index+=1
            content=serialize(answer.model_dump())
        result.append((role,content))
    return tuple(result)


ATTRIBUTED_EXAMPLES = _consistent_examples()
