"""Granskningen av en anteckning: tre parallella LLM-anrop (kvitton, det som
saknas, andra blick på besluten), kvittokontroll och cache."""
from __future__ import annotations

import asyncio
import logging
import time

from backend.cache import AuditCache, audit_key
from backend.config import Settings
from backend.data import DataStore
from backend.guardrails import check_claims, check_coverage, check_flags, summarize
from backend.llm import LLMError, StructuredLLM
from backend.prompts import CLAIMS_SYSTEM, COVERAGE_SYSTEM, DECISIONS_SYSTEM, PROMPT_VERSION
from backend.schemas import AuditMeta, AuditRequest, AuditResult, ClaimsLLM, CoverageLLM, DecisionsLLM
from backend.transcript import render_for_prompt
from backend.visit import VisitError, audit_content, resolve_visit

log = logging.getLogger("receipts.audit")

AUDIT_TIMEOUT_S = 300


class AuditError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def check_access(settings: Settings, allow_live: bool) -> None:
    """Inspelade svar kostar inget och kräver ingen kod; riktiga anrop kan kräva det."""
    if settings.replay:
        return
    if settings.demo_offline:
        raise AuditError("This demo is offline, and this review has not been precomputed.", 409)
    if not allow_live:
        raise AuditError("Live review requires an access code.", 401)


def raise_for(result: object, what: str) -> None:
    if isinstance(result, LLMError):
        raise AuditError(str(result), result.status_code) from result
    if isinstance(result, BaseException):
        log.error("%s failed: %r", what, result)
        raise AuditError(f"The {what} failed unexpectedly. Try again.", 502) from result


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
    try:
        lines, chief_complaint = resolve_visit(req, store)
    except VisitError as exc:
        raise AuditError(str(exc), exc.status_code) from exc
    note_text = req.note_text

    key = audit_key(settings.model_label, PROMPT_VERSION, render_for_prompt(lines), note_text)
    cached = cache.get(key)
    if cached is not None:
        cached.meta.cached = True
        cached.meta.latency_ms = int((time.perf_counter() - started) * 1000)
        return cached
    check_access(settings, allow_live)

    content = audit_content(lines, chief_complaint, note_text)
    # Kvittokontrollen av anteckningen är kärnan och måste lyckas. Förslagen och
    # den andra blicken är ett plus: misslyckas de visas granskningen ändå, med
    # en varning, och resultatet cachas inte så att nästa försök blir komplett.
    try:
        claims_res, coverage_res, decisions_res = await asyncio.wait_for(
            asyncio.gather(
                llm.parse(system=CLAIMS_SYSTEM, content=content, output_format=ClaimsLLM),
                llm.parse(system=COVERAGE_SYSTEM, content=content, output_format=CoverageLLM),
                llm.parse(system=DECISIONS_SYSTEM, content=content, output_format=DecisionsLLM),
                return_exceptions=True,
            ),
            timeout=AUDIT_TIMEOUT_S,
        )
    except asyncio.TimeoutError as exc:
        raise AuditError("The review took too long. Try again in a moment.", 504) from exc
    raise_for(claims_res, "review")

    warnings: list[str] = []
    if isinstance(coverage_res, BaseException):
        log.warning("coverage call failed: %r", coverage_res)
        warnings.append("Suggestions for missing details could not be generated this time. Check the note again to retry.")
        coverage_res = CoverageLLM(additions=[], follow_up_questions=[])
    if isinstance(decisions_res, BaseException):
        log.warning("decision review failed: %r", decisions_res)
        warnings.append("The second look at decisions could not be generated this time. Check the note again to retry.")
        decisions_res = DecisionsLLM(flags=[])

    claims = check_claims(claims_res, lines, note_text)
    additions, follow_ups = check_coverage(coverage_res, lines, claims)
    result = AuditResult(
        encounter_id=req.encounter_id,
        variant=req.variant if req.encounter_id else None,
        transcript=lines,
        note_text=note_text,
        claims=claims,
        suggested_additions=additions,
        follow_up_questions=follow_ups,
        decision_flags=check_flags(decisions_res.flags, lines),
        summary=summarize(claims),
        warnings=warnings,
        meta=AuditMeta(
            model=settings.model_label,
            prompt_version=PROMPT_VERSION,
            cached=False,
            latency_ms=int((time.perf_counter() - started) * 1000),
        ),
    )
    if not warnings:
        cache.put(key, result)
    return result
