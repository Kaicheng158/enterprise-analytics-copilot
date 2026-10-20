"""Provider-independent structured Plan generation boundary.

This module contains no network client. A future DeepSeek adapter may implement
PlannerTransport, but generated content still passes through the existing local
Plan parser, validator, authorization, budgets and state machine.
"""

from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Literal, Protocol

from pydantic import Field, field_validator, model_validator

from backend.tools.models import StrictModel

from .models import Plan, PlanningRequest


PLANNER_PROMPT_VERSION = "bounded-planner-v1"
PLANNER_INSTRUCTIONS = (
    "You are a bounded planning component. Produce one JSON Plan that matches the "
    "server-provided schema. You may only propose get_postgres_sales_summary tool "
    "steps and the compare_sales_growth local computation for the supplied request. "
    "Do not execute tools, claim results, grant authorization, change server limits, "
    "or add SQL or code. The server independently validates and may reject every plan."
)
MAX_GENERATED_PLAN_BYTES = 32_768


def _canonical_sha256(value: dict) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(payload).hexdigest()


_PLAN_RESPONSE_SCHEMA = Plan.model_json_schema()
PLAN_RESPONSE_SCHEMA_SHA256 = _canonical_sha256(_PLAN_RESPONSE_SCHEMA)
PLANNER_SYSTEM_PROMPT = (
    PLANNER_INSTRUCTIONS
    + "\nReturn JSON only. The required JSON schema is:\n"
    + json.dumps(
        _PLAN_RESPONSE_SCHEMA,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
)
PLANNER_PROMPT_SHA256 = sha256(PLANNER_SYSTEM_PROMPT.encode("utf-8")).hexdigest()


class PlannerMessage(StrictModel):
    role: Literal["system", "user"]
    content: str = Field(min_length=1, max_length=8_000)


class PlannerGenerationConfig(StrictModel):
    provider: Literal["deepseek"] = "deepseek"
    model: Literal["deepseek-flash"] = "deepseek-flash"
    temperature: Literal[0.0] = 0.0
    thinking: Literal["disabled"] = "disabled"
    max_output_tokens: int = Field(default=800, ge=256, le=4_096)
    timeout_seconds: float = Field(default=30.0, gt=0, le=60, allow_inf_nan=False)
    max_provider_calls: Literal[1] = 1
    retry_count: Literal[0] = 0


class PlannerGenerationRequest(StrictModel):
    messages: tuple[PlannerMessage, PlannerMessage]
    response_schema: dict
    prompt_version: Literal["bounded-planner-v1"] = PLANNER_PROMPT_VERSION
    prompt_sha256: Literal[PLANNER_PROMPT_SHA256] = PLANNER_PROMPT_SHA256
    schema_sha256: Literal[PLAN_RESPONSE_SCHEMA_SHA256] = PLAN_RESPONSE_SCHEMA_SHA256
    config: PlannerGenerationConfig

    @model_validator(mode="after")
    def message_order_and_schema_are_server_owned(self):
        if tuple(message.role for message in self.messages) != ("system", "user"):
            raise ValueError("planner messages must be system then user")
        if self.messages[0].content != PLANNER_SYSTEM_PROMPT:
            raise ValueError("planner system prompt mismatch")
        if _canonical_sha256(self.response_schema) != PLAN_RESPONSE_SCHEMA_SHA256:
            raise ValueError("planner response schema mismatch")
        return self


class PlannerUsage(StrictModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)

    @model_validator(mode="after")
    def totals_are_consistent(self):
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("planner token totals are inconsistent")
        return self


class PlannerGenerationResponse(StrictModel):
    request_id: str = Field(min_length=1, max_length=128)
    provider: Literal["deepseek"] = "deepseek"
    model: Literal["deepseek-flash"] = "deepseek-flash"
    content: str = Field(min_length=1, max_length=MAX_GENERATED_PLAN_BYTES)
    finish_reason: Literal["stop", "length", "content_filter", "error"]
    usage: PlannerUsage | None = None
    latency_ms: int = Field(ge=0)
    retry_count: Literal[0] = 0
    estimated_cost_usd: str | None = Field(
        default=None, pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$"
    )
    pricing_version: str = "deepseek-2026-09-13"

    @field_validator("request_id")
    @classmethod
    def request_id_is_safe(cls, value: str) -> str:
        if any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("invalid planner request ID")
        return value


class PlannerTransport(Protocol):
    def generate(self, request: PlannerGenerationRequest) -> PlannerGenerationResponse: ...


class PlannerProviderError(RuntimeError):
    """Sanitized provider-protocol failure, distinct from invalid Plan JSON."""


class StructuredPlanProvider:
    """Single-use provider adapter that returns only serialized Plan content."""

    def __init__(
        self,
        transport: PlannerTransport,
        *,
        config: PlannerGenerationConfig | None = None,
    ):
        self._transport = transport
        self.config = config or PlannerGenerationConfig()
        self.calls = 0
        self.last_request: PlannerGenerationRequest | None = None
        self.last_response: PlannerGenerationResponse | None = None

    def propose(self, request: PlanningRequest) -> str:
        if self.calls >= self.config.max_provider_calls:
            raise PlannerProviderError("planner_provider_call_limit")
        generation_request = build_planner_generation_request(request, self.config)
        self.calls += 1
        self.last_request = generation_request
        response = self._transport.generate(generation_request)
        if not isinstance(response, PlannerGenerationResponse):
            raise PlannerProviderError("planner_provider_response_invalid")
        self.last_response = response
        if (
            response.provider != self.config.provider
            or response.model != self.config.model
            or response.retry_count != 0
            or response.finish_reason != "stop"
            or len(response.content.encode("utf-8")) > MAX_GENERATED_PLAN_BYTES
        ):
            raise PlannerProviderError("planner_provider_response_invalid")
        return response.content


def build_planner_generation_request(
    request: PlanningRequest,
    config: PlannerGenerationConfig | None = None,
) -> PlannerGenerationRequest:
    user_payload = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return PlannerGenerationRequest(
        messages=(
            PlannerMessage(role="system", content=PLANNER_SYSTEM_PROMPT),
            PlannerMessage(role="user", content=user_payload),
        ),
        response_schema=deepcopy(_PLAN_RESPONSE_SCHEMA),
        config=config or PlannerGenerationConfig(),
    )
