"""Tunn wrapper runt Anthropic-SDK:n: structured outputs med streaming, fallback,
robusthet, kostnadsmätning och begripliga fel.

Robusthet:
- Nätverksfel, 429 och 5xx försöks om av SDK:n (max_retries).
- Ett svar i fel format (går inte att tolka eller validera mot schemat) försöks
  om en gång; misslyckas det igen blir det ett begripligt fel (502).
- Nekade (refusal) och avklippta (max_tokens) svar ger egna felmeddelanden.

Säkerhet: nyckeln läses av SDK:n från miljön och loggas aldrig. Loggen innehåller
bara tid, tokens och kostnad, aldrig patienttext eller anropets innehåll.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol, TypeVar

import anthropic
import pydantic
from pydantic import BaseModel

from backend import usage
from backend.config import Settings

log = logging.getLogger("receipts.llm")

T = TypeVar("T", bound=BaseModel)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FORMAT_ATTEMPTS = 2
MAX_TOKENS = 32_000  # tänkandet räknas in; streaming gör stora värden säkra
REQUEST_TIMEOUT_S = 600.0


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
            self._client = anthropic.AsyncAnthropic(timeout=REQUEST_TIMEOUT_S, max_retries=2)
        return self._client

    async def parse(self, *, system: str, content: list[dict[str, Any]], output_format: type[T]) -> T:
        kwargs: dict[str, Any] = dict(
            model=self.settings.model,
            max_tokens=MAX_TOKENS,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.settings.effort_for(output_format.__name__)},
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
            async with self.client.beta.messages.stream(**kwargs) as stream:
                response = await stream.get_final_message()
        except (pydantic.ValidationError, json.JSONDecodeError, anthropic.APIResponseValidationError) as exc:
            raise FormatError(type(exc).__name__) from exc
        except anthropic.AuthenticationError as exc:
            raise LLMError("The API key is invalid. Check ANTHROPIC_API_KEY.", 500) from exc
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
        except anthropic.BadRequestError as exc:
            log.warning("Claude API 400: %s", getattr(exc, "message", "")[:200])
            raise LLMError("The Claude API rejected the request (400).", 502) from exc
        except anthropic.APIStatusError as exc:
            log.warning("Claude API status %s", exc.status_code)
            raise LLMError(f"The Claude API returned an error ({exc.status_code}).", 502) from exc

        seconds = time.perf_counter() - started
        model = getattr(response, "model", None) or self.settings.model
        call = usage.from_response(output_format.__name__, model, seconds, getattr(response, "usage", None))
        usage.record(call)
        log.info(
            "llm %s: %.1fs model=%s in=%s out=%s cache_read=%s cache_write=%s cost=$%.4f stop=%s",
            output_format.__name__, seconds, model, call.input_tokens, call.output_tokens,
            call.cache_read_tokens, call.cache_write_tokens, call.cost_usd, getattr(response, "stop_reason", None),
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


class LiveWithFallback:
    """Riktiga anrop, med inspelade svar som reserv när API:t inte svarar.

    Gäller bara fel som ligger utanför vår kontroll (nätverk, överbelastning,
    tidsgräns, 5xx) och bara när en inspelning finns för exakt samma förfrågan.
    Reserven märks i kostnadsredovisningen så att den aldrig tas för ett riktigt svar.
    """

    FALLBACK_STATUSES = {429, 502, 503, 504}

    def __init__(self, live: ClaudeLLM, recorded: Any) -> None:
        self.live, self.recorded = live, recorded

    async def parse(self, *, system: str, content: list[dict[str, Any]], output_format: type[T]) -> T:
        try:
            return await self.live.parse(system=system, content=content, output_format=output_format)
        except LLMError as exc:
            if exc.status_code not in self.FALLBACK_STATUSES or not self.recorded.path_for(output_format.__name__, content).exists():
                raise
            log.warning("llm %s: live call failed (%s), using the recorded answer", output_format.__name__, exc.status_code)
            result = self.recorded.load(content, output_format)
            usage.record(usage.CallUsage(task=output_format.__name__, model="recorded", seconds=0, source="recorded-fallback"))
            return result


class RecordedFirst:
    """Inspelade svar först, riktiga anrop för allt annat.

    De inbyggda demobesöken (demo/cases/) har förberäknade svar. Med den här
    ordningen svarar de direkt även när en API-nyckel finns, så demon inte väntar
    på eller riskerar ett live-anrop. En förfrågan som inte är inspelad (en ändrad
    anteckning, en egen fråga) går till API:t som vanligt. Svaren märks "recorded"
    i kostnadsredovisningen.
    """

    def __init__(self, inner: Any, recorded: Any) -> None:
        self.inner, self.recorded = inner, recorded

    async def parse(self, *, system: str, content: list[dict[str, Any]], output_format: type[T]) -> T:
        if self.recorded.path_for(output_format.__name__, content).exists():
            return await self.recorded.parse(system=system, content=content, output_format=output_format)
        return await self.inner.parse(system=system, content=content, output_format=output_format)
