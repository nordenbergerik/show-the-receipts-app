"""De nya AI-stegen: besökskontroll direkt efter samtalet, journalutkast och
frågor till journalen. Alla svar går genom kvittokontrollen."""
from __future__ import annotations

import asyncio
import time

from backend.audit import AuditError, check_access, raise_for
from backend.config import Settings
from backend.data import DataStore
from backend.guardrails import check_answer, check_flags, check_missed
from backend.llm import StructuredLLM
from backend.prompts import ASK_SYSTEM, DRAFT_SYSTEM, PROMPT_VERSION, VISIT_CHECK_SYSTEM
from backend.schemas import (
    AskLLM,
    AskRequest,
    AskResult,
    AuditMeta,
    DraftNoteLLM,
    DraftNoteRequest,
    DraftNoteResult,
    VisitCheckLLM,
    VisitCheckRequest,
    VisitCheckResult,
)
from backend.visit import VisitError, ask_content, draft_content, resolve_visit, visit_check_content

STEP_TIMEOUT_S = 660


def _meta(settings: Settings, started: float) -> AuditMeta:
    return AuditMeta(model=settings.model_label, prompt_version=PROMPT_VERSION, cached=False,
                     latency_ms=int((time.perf_counter() - started) * 1000))


async def _call(llm: StructuredLLM, what: str, **kwargs):
    try:
        result = await asyncio.wait_for(asyncio.gather(llm.parse(**kwargs), return_exceptions=True), timeout=STEP_TIMEOUT_S)
    except asyncio.TimeoutError as exc:
        raise AuditError(f"The {what} took too long. Try again in a moment.", 504) from exc
    raise_for(result[0], what)
    return result[0]


def _resolve(req, store: DataStore):
    try:
        return resolve_visit(req, store)
    except VisitError as exc:
        raise AuditError(str(exc), exc.status_code) from exc


async def run_visit_check(req: VisitCheckRequest, *, store: DataStore, llm: StructuredLLM, settings: Settings, allow_live: bool = True) -> VisitCheckResult:
    started = time.perf_counter()
    lines, cc = _resolve(req, store)
    check_access(settings, allow_live)
    raw: VisitCheckLLM = await _call(llm, "visit check", system=VISIT_CHECK_SYSTEM, content=visit_check_content(lines, cc), output_format=VisitCheckLLM)
    return VisitCheckResult(
        transcript=lines,
        missed=check_missed(raw.missed, lines),
        decision_flags=check_flags(raw.decision_flags, lines, prefix="V"),
        meta=_meta(settings, started),
    )


async def run_draft_note(req: DraftNoteRequest, *, store: DataStore, llm: StructuredLLM, settings: Settings, allow_live: bool = True) -> DraftNoteResult:
    started = time.perf_counter()
    lines, cc = _resolve(req, store)
    check_access(settings, allow_live)
    raw: DraftNoteLLM = await _call(llm, "note draft", system=DRAFT_SYSTEM, content=draft_content(lines, cc, req.accepted, req.note_format), output_format=DraftNoteLLM)
    note = raw.note_text.replace("\r\n", "\n").strip()
    if not note:
        raise AuditError("The note draft came back empty. Try again.", 502)
    return DraftNoteResult(note_text=note[:10_000], meta=_meta(settings, started))


async def run_ask(req: AskRequest, *, store: DataStore, llm: StructuredLLM, settings: Settings, allow_live: bool = True) -> AskResult:
    started = time.perf_counter()
    lines, cc = _resolve(req, store)
    check_access(settings, allow_live)
    content, record = ask_content(lines, cc, req.note_text, req.question)
    raw: AskLLM = await _call(llm, "search", system=ASK_SYSTEM, content=content, output_format=AskLLM)
    answer, findings, warnings = check_answer(raw, record)
    return AskResult(
        question=req.question.strip(),
        verdict=answer.verdict,
        summary=answer.summary,
        findings=findings,
        where_to_look=answer.where_to_look,
        record=record,
        warnings=warnings,
        meta=_meta(settings, started),
    )
