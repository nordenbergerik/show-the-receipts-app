"""Tunn wrapper runt Anthropic-SDK:n: structured outputs, fallback, robusthet och
begripliga fel.

Robusthet:
- Nätverksfel, 429 och 5xx försöks om av SDK:n (max_retries).
- Ett svar i fel format (går inte att tolka eller validera mot schemat) försöks
  om en gång; misslyckas det igen blir det ett begripligt fel (502).
- Nekade (refusal) och avklippta (max_tokens) svar ger egna felmeddelanden.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol, TypeVar

import anthropic
import pydantic
from pydantic import BaseModel

from backend.config import Settings

log = logging.getLogger("receipts.llm")

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FORMAT_ATTEMPTS = 2


class LLMError(Exception):
    """Fel som kan visas för användaren. status_code mappas till HTTP-svaret."""

    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class FormatError(Exception):
    """Svaret kom fram men gick inte att tolka mot schemat."""


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

        for attempt in range(1, FORMAT_ATTEMPTS + 1):
            try:
                return await self._call_once(kwargs, output_format)
            except FormatError as exc:
                log.warning("llm %s: wrong format (attempt %d/%d): %s", output_format.__name__, attempt, FORMAT_ATTEMPTS, exc)
        raise LLMError("The model answered in an unexpected format twice. Try again in a moment.", 502)

    async def _call_once(self, kwargs: dict[str, Any], output_format: type[T]) -> T:
        started = time.perf_counter()
        try:
            response = await self.client.beta.messages.parse(**kwargs)
        except (pydantic.ValidationError, json.JSONDecodeError, anthropic.APIResponseValidationError) as exc:
            raise FormatError(type(exc).__name__) from exc
        except anthropic.AuthenticationError as exc:
            raise LLMError("The API key is invalid. Check ANTHROPIC_API_KEY in .env.", 500) from exc
        except TypeError as exc:
            # SDK:n kastar TypeError innan anropet skickas när ingen nyckel alls är satt.
            if "authentication" not in str(exc).lower():
                raise
            raise LLMError("Live review is not set up yet: no API key has been added.", 503) from exc
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

        usage = getattr(response, "usage", None)
        log.info(
            "llm %s: %.1fs in=%s out=%s cache_read=%s stop=%s",
            output_format.__name__,
            time.perf_counter() - started,
            getattr(usage, "input_tokens", None),
            getattr(usage, "output_tokens", None),
            getattr(usage, "cache_read_input_tokens", None),
            getattr(response, "stop_reason", None),
        )

        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            raise LLMError("The model declined to review this text.", 422)
        if stop_reason == "max_tokens":
            raise LLMError("The answer was too long and was cut off. Try a shorter note.", 502)
        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise FormatError("no parsed output")
        if not isinstance(parsed, output_format):
            try:
                parsed = output_format.model_validate(parsed)
            except pydantic.ValidationError as exc:
                raise FormatError("schema mismatch") from exc
        return parsed
