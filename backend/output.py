"""Provider-independent analytics response schema and strict JSON parsing."""
import json
from pydantic import BaseModel, ConfigDict


class AnalyticsAnswer(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    summary: str
    facts: list[str]
    interpretation: list[str]
    limitations: list[str]


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-standard JSON constant")


def parse_answer(content: str) -> AnalyticsAnswer:
    """Reject malformed/ambiguous JSON and schema violations without repair."""
    data = json.loads(content, object_pairs_hook=_unique_object,
                      parse_constant=_reject_constant)
    return AnalyticsAnswer.model_validate(data)
