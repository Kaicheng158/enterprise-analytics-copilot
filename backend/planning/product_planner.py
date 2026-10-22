"""Bounded multi-request wrapper around the existing DeepSeek Planner adapter."""

from __future__ import annotations

from decimal import Decimal
from threading import Lock
from typing import Callable
from urllib.request import urlopen

from pydantic import Field

from backend.tools.models import StrictModel

from .deepseek_planner_adapter import (
    DeepSeekPlannerSettings,
    DeepSeekStructuredPlannerTransport,
)
from .models import PlanningRequest
from .planner_safety import PlannerCostGuard
from .structured_provider import (
    PLAN_RESPONSE_SCHEMA_SHA256,
    PLANNER_PROMPT_SHA256,
    PLANNER_PROMPT_VERSION,
    StructuredPlanProvider,
)


class ProductPlannerTelemetry(StrictModel):
    provider: str = "deepseek"
    model: str = "deepseek-flash"
    prompt_version: str = PLANNER_PROMPT_VERSION
    prompt_sha256: str = PLANNER_PROMPT_SHA256
    schema_sha256: str = PLAN_RESPONSE_SCHEMA_SHA256
    calls_started: int = Field(ge=0)
    calls_completed: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    estimated_cost_usd: str = Field(pattern=r"^(0|[1-9][0-9]*)(\.[0-9]+)?$")
    stopped: bool


class DeepSeekProductPlanProvider:
    """Create one official transport per new task under one server budget guard.

    The existing durable Planner checks idempotency before invoking this object,
    so a repeated request does not consume another provider call. Calls are
    serialized because PlannerCostGuard intentionally permits one pending call.
    """

    def __init__(
        self,
        settings: DeepSeekPlannerSettings,
        *,
        budget_usd: Decimal = Decimal("0.02"),
        per_call_reserve_usd: Decimal = Decimal("0.002"),
        max_calls: int = 10,
        opener: Callable = urlopen,
    ):
        if not settings.enabled:
            raise ValueError("llm_planner_disabled")
        self.settings = settings
        self._opener = opener
        self._guard = PlannerCostGuard(
            budget_usd=budget_usd,
            per_call_reserve_usd=per_call_reserve_usd,
            max_calls=max_calls,
        )
        self._lock = Lock()
        self._completed = 0
        self._input_tokens = 0
        self._output_tokens = 0
        self._total_tokens = 0
        self._latency_ms = 0

    def propose(self, request: PlanningRequest) -> str:
        with self._lock:
            transport = DeepSeekStructuredPlannerTransport(
                self.settings,
                cost_guard=self._guard,
                opener=self._opener,
            )
            provider = StructuredPlanProvider(transport)
            content = provider.propose(request)
            response = provider.last_response
            if response is None or response.usage is None:
                raise RuntimeError("planner_telemetry_missing")
            self._completed += 1
            self._input_tokens += response.usage.input_tokens
            self._output_tokens += response.usage.output_tokens
            self._total_tokens += response.usage.total_tokens
            self._latency_ms += response.latency_ms
            return content

    def telemetry(self) -> ProductPlannerTelemetry:
        with self._lock:
            return ProductPlannerTelemetry(
                calls_started=self._guard.calls_started,
                calls_completed=self._completed,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                total_tokens=self._total_tokens,
                latency_ms=self._latency_ms,
                estimated_cost_usd=format(self._guard.spent_usd, "f"),
                stopped=self._guard.stopped,
            )


__all__ = ["DeepSeekProductPlanProvider", "ProductPlannerTelemetry"]
