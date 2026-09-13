"""Small provider boundary; only DeepSeek is implemented in Phase 1."""
import json
import logging
import os
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
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code


class LLMProvider(Protocol):
    def chat(self, message: str) -> ChatResult: ...


class DeepSeekProvider:
    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    def chat(self, message: str) -> ChatResult:
        if not self.api_key:
            raise ProviderError(503, "LLM provider is not configured")
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
            status = 503 if error.code in (401, 402, 403, 429) else 502
            raise ProviderError(status, "LLM provider request failed") from None
        except (TimeoutError, socket.timeout):
            raise ProviderError(504, "LLM provider timed out") from None
        except URLError:
            raise ProviderError(502, "LLM provider is unavailable") from None
        except (ValueError, UnicodeError):
            raise ProviderError(502, "Invalid LLM provider response") from None
        try:
            choice = data["choices"][0]
            answer = choice["message"]["content"]
            if not isinstance(answer, str) or not answer.strip():
                raise ValueError("Empty answer")
            raw_usage = data["usage"]
            usage = TokenUsage(**{
                **raw_usage,
                "reasoning_tokens": (raw_usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
            })
            result = ChatResult(
                answer=answer, provider="deepseek", model=data["model"], usage=usage,
                latency_ms=round((time.monotonic() - started) * 1000),
                finish_reason=choice["finish_reason"],
            )
        except (KeyError, IndexError, TypeError, ValueError, ValidationError):
            raise ProviderError(502, "Invalid LLM provider response") from None
        logger.info("llm_usage %s", json.dumps(result.model_dump(exclude={"answer"})))
        return result


def get_provider() -> LLMProvider:
    return DeepSeekProvider(
        api_key=os.getenv("DEEPSEEK_API_KEY", ""),
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
    )
