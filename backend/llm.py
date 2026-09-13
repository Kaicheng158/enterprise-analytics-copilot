"""Small provider boundary; only DeepSeek is implemented in Phase 1."""
import json
import logging
import os
from http.client import HTTPException as HTTPTransportError
import socket
import time
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
logger = logging.getLogger("uvicorn.error")


class TokenUsage(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    prompt_cache_hit_tokens: int | None = Field(default=None, ge=0)
    prompt_cache_miss_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)


class ChatResult(BaseModel):
    answer: str
    provider: str
    model: str
    usage: TokenUsage
    latency_ms: int
    finish_reason: str


class ProviderError(Exception):
    def __init__(self, status_code: int, message: str, code: str = "llm_provider_error"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class LLMProvider(Protocol):
    def chat(self, message: str) -> ChatResult: ...


class DeepSeekProvider:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def chat(self, message: str) -> ChatResult:
        if not self.api_key:
            raise ProviderError(503, "LLM provider is not configured", "llm_not_configured")
        request = Request(
            "https://api.deepseek.com/chat/completions",
            data=json.dumps({
                "model": self.model,
                "messages": [{"role": "user", "content": message}],
                "stream": False,
                "thinking": {"type": "disabled"},
                "max_tokens": 512,
            }).encode(),
            headers={"Authorization": "Bearer " + self.api_key,
                     "Content-Type": "application/json"},
        )
        started = time.monotonic()
        try:
            with urlopen(request, timeout=30) as response:
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
            raise ProviderError(status, message, code) from None
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
                answer=answer, provider="deepseek", model=data["model"], usage=usage,
                latency_ms=round((time.monotonic() - started) * 1000),
                finish_reason=choice["finish_reason"],
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError):
            raise ProviderError(502, "Invalid LLM provider response", "llm_invalid_response") from None
        logger.info("llm_usage %s", json.dumps(result.model_dump(exclude={"answer"})))
        return result


def get_provider() -> LLMProvider:
    return DeepSeekProvider(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
    )
