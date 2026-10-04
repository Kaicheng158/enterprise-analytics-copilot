"""Provider-independent analytics response schema and strict JSON parsing."""
import json
from pydantic import BaseModel, ConfigDict, ValidationError


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


class OutputFailure(ValueError):
    """Fixed diagnostic codes only: never include generated content or field values."""
    def __init__(self, stage, reason, *, position=None, issue_types=None):
        super().__init__(reason)
        self.diagnostic = {"stage": stage, "reason": reason}
        if position is not None:
            self.diagnostic["position"] = position
        if issue_types:
            self.diagnostic["issue_types"] = sorted(set(issue_types))


def parse_answer(content: str) -> AnalyticsAnswer:
    """Reject malformed/ambiguous JSON and schema violations without repair."""
    if not isinstance(content, str):
        raise OutputFailure("content", "content_type_error")
    if not content.strip():
        raise OutputFailure("content", "empty_content")
    try:
        data = json.loads(content, object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except json.JSONDecodeError as error:
        reason = "json_parse_error" if content.lstrip().startswith(("{", "[")) else "non_json"
        raise OutputFailure("json_parse", reason, position=error.pos) from None
    except (ValueError, RecursionError):
        raise OutputFailure("json_parse", "invalid_json_structure") from None
    try:
        return AnalyticsAnswer.model_validate(data)
    except ValidationError as error:
        # Do not retain Pydantic input, loc, msg or ctx: these can contain secrets.
        mapping = {"missing": "schema_field_missing", "extra_forbidden": "extra_field"}
        issues = [mapping.get(e["type"], "type_error") for e in error.errors()]
        raise OutputFailure("schema", issues[0], issue_types=issues) from None
