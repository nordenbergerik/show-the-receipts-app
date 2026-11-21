"""Granskningspipelinen: indexera, två parallella LLM-anrop, kvittokontroll, cache."""
from __future__ import annotations

import asyncio
import logging
import time

from backend.cache import AuditCache, audit_key
from backend.config import Settings
from backend.data import DataStore
from backend.guardrails import check_claims, check_coverage, summarize
from backend.llm import LLMError, StructuredLLM
from backend.prompts import CLAIMS_SYSTEM, COVERAGE_SYSTEM, PROMPT_VERSION, note_block, transcript_block
from backend.schemas import AuditMeta, AuditRequest, AuditResult, ClaimsLLM, CoverageLLM
from backend.transcript import index_transcript, render_for_prompt


log = logging.getLogger("receipts.audit")

AUDIT_TIMEOUT_S = 300


class AuditError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def _resolve_transcript(req: AuditRequest, store: DataStore) -> tuple[str, str]:
    """Returnerar (dialog, huvudbesvär)."""
    if req.transcript_text:
        return req.transcript_text, ""
    enc = store.get(req.encounter_id or "")
    if enc is None:
        raise AuditError(f"Unknown visit: {req.encounter_id}", 404)
    dialogue = enc.variants.get(req.variant)
    if dialogue is None:
        raise AuditError(f"{enc.encounter_id} has no '{req.variant}' transcript", 404)
    return dialogue, enc.chief_complaint


async def run_audit(
    req: AuditRequest,
    *,
    store: DataStore,
    llm: StructuredLLM,
    cache: AuditCache,
    settings: Settings,
    allow_live: bool = True,
) -> AuditResult:
    started = time.perf_counter()
    dialogue, chief_complaint = _resolve_transcript(req, store)
    lines = index_transcript(dialogue)
    if not lines:
        raise AuditError("The transcript is empty.", 400)
    rendered = render_for_prompt(lines)
    note_text = req.note_text

    key = audit_key(settings.model, PROMPT_VERSION, rendered, note_text)
    cached = cache.get(key)
    if cached is not None:
        cached.meta.cached = True
        cached.meta.latency_ms = int((time.perf_counter() - started) * 1000)
        return cached

    if settings.demo_offline:
        raise AuditError("This demo is offline, and this review has not been precomputed.", 409)
    if not allow_live:
        raise AuditError("Live review requires an access code.", 401)

    # Samtalet ligger först och cachas (prompt caching), så att omgranskningar
    # av samma samtal med en ändrad anteckning återanvänder prefixet.
    content = [
        {"type": "text", "text": transcript_block(rendered, chief_complaint), "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": note_block(note_text)},
    ]
    # Kvittokontrollen av anteckningen är kärnan och måste lyckas. Förslagen
    # (tillägg och frågor) är ett plus: misslyckas de visas granskningen ändå,
    # med en varning, och resultatet cachas inte så att nästa försök blir komplett.
    try:
        claims_res, coverage_res = await asyncio.wait_for(
            asyncio.gather(
                llm.parse(system=CLAIMS_SYSTEM, content=content, output_format=ClaimsLLM),
                llm.parse(system=COVERAGE_SYSTEM, content=content, output_format=CoverageLLM),
                return_exceptions=True,
            ),
            timeout=AUDIT_TIMEOUT_S,
        )
    except asyncio.TimeoutError as exc:
        raise AuditError("The review took too long. Try again in a moment.", 504) from exc

    if isinstance(claims_res, LLMError):
        raise AuditError(str(claims_res), claims_res.status_code) from claims_res
    if isinstance(claims_res, BaseException):
        log.exception("claims call failed", exc_info=claims_res)
        raise AuditError("The review failed unexpectedly. Try again.", 502) from claims_res
    claims_llm = claims_res

    warnings: list[str] = []
    if isinstance(coverage_res, BaseException):
        log.warning("coverage call failed: %s", type(coverage_res).__name__)
        warnings.append("Suggestions for missing details could not be generated this time. Check the note again to retry.")
        coverage_llm = CoverageLLM(additions=[], follow_up_questions=[])
    else:
        coverage_llm = coverage_res

    claims = check_claims(claims_llm, lines, note_text)
    additions, follow_ups = check_coverage(coverage_llm, lines, claims)
    result = AuditResult(
        encounter_id=req.encounter_id,
        variant=req.variant if req.encounter_id else None,
        transcript=lines,
        note_text=note_text,
        claims=claims,
        suggested_additions=additions,
        follow_up_questions=follow_ups,
        summary=summarize(claims),
        warnings=warnings,
        meta=AuditMeta(
            model=settings.model,
            prompt_version=PROMPT_VERSION,
            cached=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
        ),
    )
    if not warnings:
        cache.put(key, result)
    return result
