"""Small provider boundary; only DeepSeek is implemented in Phase 1."""
import json
import random
import uuid
from datetime import datetime, timezone
import logging
from http.client import HTTPException as HTTPTransportError
import socket
import time
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from backend.prompts import build_messages
from backend.pricing import PRICING_VERSION, estimate_cost

from pydantic import BaseModel, Field, ValidationError, model_validator

from backend.config import (
    BACKOFF_CAP_SECONDS, BACKOFF_JITTER_FRACTION, COST_CURRENCY,
    LLMSettings, PROVIDERS, RETRYABLE_HTTP_STATUSES, RetryConfig, load_settings,
)
logger = logging.getLogger("uvicorn.error")


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(ge=0, strict=True)
    completion_tokens: int = Field(ge=0, strict=True)
    total_tokens: int = Field(ge=0, strict=True)
    prompt_cache_hit_tokens: int | None = Field(default=None, ge=0, strict=True)
    prompt_cache_miss_tokens: int | None = Field(default=None, ge=0, strict=True)
    reasoning_tokens: int | None = Field(default=None, ge=0, strict=True)


    @model_validator(mode="after")
    def consistent_counts(self):
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("Inconsistent total tokens")
        hit, miss = self.prompt_cache_hit_tokens, self.prompt_cache_miss_tokens
        if hit is not None and hit > self.prompt_tokens:
            raise ValueError("Invalid cache hit count")
        if miss is not None and miss > self.prompt_tokens:
            raise ValueError("Invalid cache miss count")
        if hit is not None and miss is not None and hit + miss != self.prompt_tokens:
            raise ValueError("Inconsistent cache tokens")
        if self.reasoning_tokens is not None and self.reasoning_tokens > self.completion_tokens:
            raise ValueError("Invalid reasoning token count")
        return self


class ChatResult(BaseModel):
    answer: str
    provider: str
    model: str
    usage: TokenUsage
    latency_ms: int
    finish_reason: str
    request_id: str = ""
    retry_count: int = 0
    estimated_cost: str | None = None
    cost_currency: str = COST_CURRENCY
    pricing_version: str = PRICING_VERSION
    pricing_tier: str | None = None
    cost_complete: bool = False


class ProviderError(Exception):
    def __init__(self, status_code: int, message: str, code: str = "llm_provider_error", retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable


class LLMProvider(Protocol):
    def chat(self, message: str) -> ChatResult: ...


class DeepSeekProvider:
    def __init__(self, api_key: str, model: str, config: RetryConfig | None = None, settings: LLMSettings | None = None):
        self.api_key = api_key
        self.model = model
        self.settings = settings or LLMSettings(model=model)
        self.config = config or self.settings
        self.provider = self.settings.provider

    def chat(self, message: str) -> ChatResult:
        request_id = str(uuid.uuid4())
        started = time.monotonic()
        for attempt in range(self.config.max_retries + 1):
            attempt_start = time.monotonic()
            utc_start = datetime.now(timezone.utc)
            try:
                result = self._chat_once(message)
            except ProviderError as error:
                record = {
                    "request_id": request_id, "provider": self.provider, "model": self.model,
                    "input_tokens": None, "output_tokens": None, "total_tokens": None,
                    "estimated_cost": None, "cost_currency": COST_CURRENCY, "pricing_version": PRICING_VERSION,
                    "latency_ms": round((time.monotonic() - attempt_start) * 1000),
                    "retry_count": attempt, "status": "error", "error_code": error.code,
                }
                logger.info("llm_attempt %s", json.dumps(record))
                if not error.retryable or attempt == self.config.max_retries:
                    record["latency_ms"] = round((time.monotonic() - started) * 1000)
                    record["cost_complete"] = False
                    logger.info("llm_request %s", json.dumps(record))
                    raise
                # Bounded exponential backoff with jitter; no unbounded retry loop.
                delay = min(BACKOFF_CAP_SECONDS, self.config.backoff_seconds * 2 ** attempt)
                time.sleep(delay + random.uniform(0, delay * BACKOFF_JITTER_FRACTION))
                continue
            amount, tier = estimate_cost(self.provider, result.model, result.usage, utc_start)
            result.request_id = request_id
            result.retry_count = attempt
            result.estimated_cost = amount
            result.pricing_tier = tier
            result.cost_complete = attempt == 0 and amount is not None
            record = {
                "request_id": request_id, "provider": result.provider, "model": result.model,
                "input_tokens": result.usage.prompt_tokens,
                "output_tokens": result.usage.completion_tokens,
                "total_tokens": result.usage.total_tokens,
                "usage": result.usage.model_dump(), "estimated_cost": amount,
                "cost_currency": COST_CURRENCY, "pricing_version": PRICING_VERSION,
                "pricing_tier": tier, "retry_count": attempt, "status": "success",
                "latency_ms": round((time.monotonic() - attempt_start) * 1000),
            }
            logger.info("llm_attempt %s", json.dumps(record))
            result.latency_ms = round((time.monotonic() - started) * 1000)
            record.update(latency_ms=result.latency_ms, cost_complete=result.cost_complete)
            logger.info("llm_request %s", json.dumps(record))
            return result
        raise AssertionError("Unreachable retry state")

    def _chat_once(self, message: str) -> ChatResult:
        if not self.api_key:
            raise ProviderError(503, "LLM provider is not configured", "llm_not_configured")
        request = Request(
            PROVIDERS[self.provider]["chat_url"],
            data=json.dumps({
                "model": self.model,
                "messages": build_messages(message),
                "stream": False,
                "thinking": {"type": self.settings.thinking},
                "max_tokens": self.settings.max_output_tokens,
            }).encode(),
            headers={"Authorization": "Bearer " + self.api_key,
                     "Content-Type": "application/json"},
        )
        started = time.monotonic()
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                data = json.load(response)
        except HTTPError as error:
            # Never return upstream bodies, headers or credentials.
            errors = {
                401: (503, "llm_authentication_failed", "LLM provider authentication failed"),
                403: (503, "llm_access_denied", "LLM provider access denied"),
                402: (503, "llm_insufficient_balance", "LLM provider balance unavailable"),
                429: (503, "llm_rate_limited", "LLM provider rate limit reached"),
                400: (502, "llm_request_rejected", "LLM provider rejected the request"),
                422: (502, "llm_request_rejected", "LLM provider rejected the request"),
            }
            status, code, message = errors.get(
                error.code, (502, "llm_upstream_error", "LLM provider request failed")
            )
            error.close()
            raise ProviderError(status, message, code, retryable=error.code in RETRYABLE_HTTP_STATUSES) from None
        except (TimeoutError, socket.timeout):
            raise ProviderError(504, "LLM provider timed out", "llm_timeout") from None
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise ProviderError(504, "LLM provider timed out", "llm_timeout") from None
            raise ProviderError(502, "LLM provider is unavailable", "llm_connection_failed") from None
        except (OSError, HTTPTransportError):
            raise ProviderError(502, "LLM provider connection interrupted", "llm_connection_failed") from None
        except (ValueError, UnicodeError):
            raise ProviderError(502, "Invalid LLM provider response", "llm_invalid_response") from None
        try:
            choice = data["choices"][0]
            answer = choice["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("Empty answer")
            raw_usage = data["usage"]
            if not isinstance(raw_usage, dict):
                raise ValueError("Usage must be an object")
            details = raw_usage.get("completion_tokens_details")
            if details is not None and not isinstance(details, dict):
                raise ValueError("Token details must be an object")
            usage = TokenUsage(**{
                **raw_usage,
                "reasoning_tokens": (details or {}).get("reasoning_tokens"),
            })
            result = ChatResult(
                answer=answer, provider=self.provider, model=data["model"], usage=usage,
                latency_ms=round((time.monotonic() - started) * 1000),
                finish_reason=choice["finish_reason"],
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError):
            raise ProviderError(502, "Invalid LLM provider response", "llm_invalid_response") from None
        return result


# Registration boundary for future adapters; no other provider is implemented.
PROVIDER_ADAPTERS = {"deepseek": DeepSeekProvider}


def get_provider() -> LLMProvider:
    try:
        settings = load_settings()
        adapter = PROVIDER_ADAPTERS[settings.provider]
    except (ValueError, KeyError):
        raise ProviderError(503, "Invalid LLM configuration", "llm_invalid_config") from None
    return adapter(api_key=settings.api_key.get_secret_value(), model=settings.model, settings=settings)
