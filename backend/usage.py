"""Tokens, tid och kostnad per AI-anrop.

Varje anrop loggar sin förbrukning i en lista som hör till den pågående
förfrågan (contextvar), så att API:t kan redovisa vad varje steg kostade.
Priserna är Anthropics listpriser i USD per miljon tokens; skrivning till
prompt-cachen debiteras som 1,25 x inpris.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Any

from pydantic import BaseModel

# USD per miljon tokens: (in, ut, läsning ur cache)
PRICES: dict[str, tuple[float, float, float]] = {
    "claude-fable-5-2": (8.00, 40.00, 0.20),
    "claude-mythos-5-2": (8.00, 40.00, 0.20),
    "claude-fable-5-1": (10.00, 50.00, 1.00),
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}
CACHE_WRITE_FACTOR = 1.25


class CallUsage(BaseModel):
    task: str
    model: str
    seconds: float
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    source: str = "live"  # live | recorded | recorded-fallback


class StepUsage(BaseModel):
    calls: list[CallUsage]
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float
    source: str


_current: ContextVar[list[CallUsage] | None] = ContextVar("usage", default=None)


def start() -> list[CallUsage]:
    calls: list[CallUsage] = []
    _current.set(calls)
    return calls


def record(call: CallUsage) -> None:
    calls = _current.get()
    if calls is not None:
        calls.append(call)


def cost(model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> float:
    price_in, price_out, price_cache = PRICES.get(model, PRICES["claude-fable-5-2"])
    total = (
        input_tokens * price_in
        + output_tokens * price_out
        + cache_read * price_cache
        + cache_write * price_in * CACHE_WRITE_FACTOR
    ) / 1_000_000
    return round(total, 4)


def from_response(task: str, model: str, seconds: float, usage: Any) -> CallUsage:
    i = int(getattr(usage, "input_tokens", 0) or 0)
    o = int(getattr(usage, "output_tokens", 0) or 0)
    cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cw = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return CallUsage(task=task, model=model, seconds=round(seconds, 1), input_tokens=i, output_tokens=o,
                     cache_read_tokens=cr, cache_write_tokens=cw, cost_usd=cost(model, i, o, cr, cw))


def summarize(calls: list[CallUsage]) -> StepUsage | None:
    if not calls:
        return None
    sources = {c.source for c in calls}
    source = "live" if sources == {"live"} else ("recorded" if sources == {"recorded"} else "mixed")
    return StepUsage(
        calls=calls,
        input_tokens=sum(c.input_tokens for c in calls),
        output_tokens=sum(c.output_tokens for c in calls),
        cache_read_tokens=sum(c.cache_read_tokens for c in calls),
        cache_write_tokens=sum(c.cache_write_tokens for c in calls),
        cost_usd=round(sum(c.cost_usd for c in calls), 4),
        source=source,
    )
