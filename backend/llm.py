"""Tunn wrapper runt Anthropic-SDK:n: structured outputs, fallback och begripliga fel."""
from __future__ import annotations

import logging
import time
from typing import Any, Protocol, TypeVar

import anthropic
from pydantic import BaseModel

from backend.config import Settings

log = logging.getLogger("receipts.llm")

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class LLMError(Exception):
    """Fel som kan visas för användaren. status_code mappas till HTTP-svaret."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class StructuredLLM(Protocol):
    async def parse(self, *, system: str, content: list[dict[str, Any]], output_format: type[T]) -> T: ...


class ClaudeLLM:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: anthropic.AsyncAnthropic | None = None

    @property
    def client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            self._client = anthropic.AsyncAnthropic(timeout=180.0, max_retries=2)
        return self._client

    async def parse(self, *, system: str, content: list[dict[str, Any]], output_format: type[T]) -> T:
        kwargs: dict[str, Any] = dict(
            model=self.settings.model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.settings.effort},
            output_format=output_format,
        )
        if self.settings.use_fallbacks:
            # Om modellen nekar körs samma anrop om på en annan modell (server-side).
            kwargs.update(betas=[FALLBACK_BETA], fallbacks="default")

        started = time.perf_counter()
        try:
            response = await self.client.beta.messages.parse(**kwargs)
        except anthropic.AuthenticationError as exc:
            raise LLMError("The API key is missing or invalid. Check ANTHROPIC_API_KEY in .env.", 500) from exc
        except anthropic.PermissionDeniedError as exc:
            raise LLMError("The API key does not have access to this model.", 500) from exc
        except anthropic.RateLimitError as exc:
            raise LLMError("Too many requests right now. Try again in a moment.", 429) from exc
        except anthropic.APITimeoutError as exc:
            raise LLMError("Claude did not respond in time. Try again.", 504) from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("Could not reach the Claude API. Check the network connection.", 503) from exc
        except anthropic.APIStatusError as exc:
            log.warning("Claude API status %s", exc.status_code)
            raise LLMError(f"The Claude API returned an error ({exc.status_code}).", 502) from exc

        usage = response.usage
        log.info(
            "llm %s: %.1fs in=%s out=%s cache_read=%s stop=%s",
            output_format.__name__,
            time.perf_counter() - started,
            usage.input_tokens,
            usage.output_tokens,
            getattr(usage, "cache_read_input_tokens", None),
            response.stop_reason,
        )

        if response.stop_reason == "refusal":
            raise LLMError("The model declined to review this text.", 422)
        if response.stop_reason == "max_tokens":
            raise LLMError("The response was too long and was cut off. Try a shorter note.", 502)
        parsed = response.parsed_output
        if parsed is None:
            raise LLMError("The model did not return a valid structured response.", 502)
        return parsed
