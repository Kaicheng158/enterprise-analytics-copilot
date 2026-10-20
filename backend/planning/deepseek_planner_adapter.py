"""Default-off DeepSeek Chat Completions transport for structured Plans."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from http.client import HTTPException as HTTPTransportError
import json
import os
from pathlib import Path
import socket
import time
from typing import Callable, Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

from backend.config import PRICES, PRICING_VERSION, PROVIDERS

from .planner_safety import PlannerCostGuard, PlannerSafetyStop
from .structured_provider import (
    PlannerGenerationRequest,
    PlannerGenerationResponse,
    PlannerProviderError,
    PlannerUsage,
)


MAX_HTTP_RESPONSE_BYTES = 1_000_000


class DeepSeekPlannerSettings(BaseModel):
    enabled: bool = False
    api_key: SecretStr = SecretStr("")
    model: Literal["deepseek-flash"] = "deepseek-flash"
    timeout_seconds: float = Field(default=30, gt=0, le=60, allow_inf_nan=False)
    max_output_tokens: int = Field(default=800, ge=256, le=4096)
    thinking: Literal["disabled"] = "disabled"


def load_deepseek_planner_settings() -> DeepSeekPlannerSettings:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
    raw = os.getenv("LLM_PLANNER_ENABLED", "false").strip().lower()
    values = {"true": True, "false": False, "1": True, "0": False}
    if raw not in values:
        raise ValueError("Invalid LLM_PLANNER_ENABLED value")
    return DeepSeekPlannerSettings(
        enabled=values[raw],
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
    )


class DeepSeekStructuredPlannerTransport:
    def __init__(
        self,
        settings: DeepSeekPlannerSettings,
        *,
        cost_guard: PlannerCostGuard,
        opener: Callable = urlopen,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not settings.enabled:
            raise ValueError("llm_planner_disabled")
        if not settings.api_key.get_secret_value():
            raise ValueError("llm_planner_not_configured")
        if settings.model != "deepseek-flash" or settings.thinking != "disabled":
            raise ValueError("llm_planner_config_invalid")
        self.settings = settings
        self.cost_guard = cost_guard
        self._opener = opener
        self._clock = clock
        self.calls = 0

    def generate(self, request: PlannerGenerationRequest) -> PlannerGenerationResponse:
        if self.calls >= 1:
            raise PlannerProviderError("planner_provider_call_limit")
        if (
            request.config.provider != "deepseek"
            or request.config.model != self.settings.model
            or request.config.max_output_tokens != self.settings.max_output_tokens
            or request.config.thinking != self.settings.thinking
            or request.config.retry_count != 0
        ):
            raise PlannerProviderError("planner_generation_config_mismatch")
        self.cost_guard.begin_call()
        self.calls += 1
        payload = {
            "model": self.settings.model,
            "messages": [item.model_dump(mode="json") for item in request.messages],
            "response_format": {"type": "json_object"},
            "stream": False,
            "thinking": {"type": self.settings.thinking},
            "max_tokens": self.settings.max_output_tokens,
        }
        http_request = Request(
            PROVIDERS["deepseek"]["chat_url"],
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": "Bearer " + self.settings.api_key.get_secret_value(),
                "Content-Type": "application/json",
            },
            method="POST",
        )
        started = self._clock()
        try:
            with self._opener(http_request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read(MAX_HTTP_RESPONSE_BYTES + 1)
            if len(raw) > MAX_HTTP_RESPONSE_BYTES:
                raise PlannerProviderError("planner_provider_response_too_large")
            data = json.loads(raw.decode("utf-8"))
            result = self._parse(data, round((self._clock() - started) * 1000))
            cost = self._conservative_cost(result.usage)
            self.cost_guard.complete_call(cost)
            return result.model_copy(update={"estimated_cost_usd": cost})
        except PlannerSafetyStop:
            raise
        except (HTTPError, URLError, TimeoutError, socket.timeout, HTTPTransportError):
            self.cost_guard.abort_call()
            raise PlannerProviderError("planner_provider_transport_error") from None
        except (json.JSONDecodeError, UnicodeError, KeyError, TypeError, ValueError, IndexError):
            self.cost_guard.abort_call()
            raise PlannerProviderError("planner_provider_response_invalid") from None
        except PlannerProviderError:
            self.cost_guard.abort_call()
            raise

    def _parse(self, data: dict, latency_ms: int) -> PlannerGenerationResponse:
        choices = data["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("invalid choices")
        choice = choices[0]
        message = choice["message"]
        if message.get("role") != "assistant" or message.get("tool_calls"):
            raise ValueError("invalid planner message")
        usage = data["usage"]
        parsed_usage = PlannerUsage(
            input_tokens=usage["prompt_tokens"],
            output_tokens=usage["completion_tokens"],
            total_tokens=usage["total_tokens"],
        )
        return PlannerGenerationResponse(
            request_id=data["id"],
            provider="deepseek",
            model=data["model"],
            content=message["content"],
            finish_reason=choice["finish_reason"],
            usage=parsed_usage,
            latency_ms=max(0, latency_ms),
            retry_count=0,
        )

    @staticmethod
    def _conservative_cost(usage: PlannerUsage | None) -> str:
        if usage is None:
            raise PlannerSafetyStop("planner_cost_evidence_missing")
        rates = PRICES[("deepseek", "deepseek-flash")]["peak"]
        amount = (
            Decimal(usage.input_tokens) * Decimal(rates["miss"])
            + Decimal(usage.output_tokens) * Decimal(rates["output"])
        ) / Decimal(1_000_000)
        return format(amount, "f")
