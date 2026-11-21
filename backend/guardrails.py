"""Deterministisk kvittokontroll: LLM:en föreslår, koden avgör.

- Ett kvitto (evidence) godtas bara om rad-ID:t finns och citatet faktiskt
  står på den raden. Påståenden som förlorar alla kvitton blir "unsupported".
- Varje påstående kopplas till en position i anteckningen (note_span) så att
  UI:t kan markera det.
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

from rapidfuzz import fuzz

from backend.schemas import (
    Addition,
    AskLLM,
    Claim,
    ClaimsLLM,
    CoverageLLM,
    DecisionFlag,
    DecisionFlagLLM,
    Evidence,
    EvidenceLLM,
    Finding,
    FollowUp,
    MissedItemLLM,
    MissedItem,
    NoteSpan,
    Summary,
)
from backend.transcript import Line

QUOTE_THRESHOLD = 90  # fuzzy-tröskel för att ett citat ska räknas som att det står på raden
SPAN_THRESHOLD = 85  # fuzzy-tröskel för att hitta påståendets position i anteckningen
DUPLICATE_THRESHOLD = 90  # tillägg som i princip upprepar ett befintligt påstående tas bort

# Rimlighetsgränser: ett svar som spårat ur ska inte kunna fylla gränssnittet.
MAX_CLAIMS = 300
MAX_ADDITIONS = 8
MAX_FOLLOW_UPS = 6
MAX_TEXT = 600
MAX_FLAGS = 4
MAX_MISSED = 6
MAX_FINDINGS = 6
MAX_WHERE = 3

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Gemener, utan skiljetecken och med enkla mellanslag.

    ACI-Bench är tokeniserat ("hi , brian .") medan LLM:en citerar naturligt
    ("hi, brian."); normaliseringen gör dem jämförbara.
    """
    text = unicodedata.normalize("NFKC", text).lower()
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def quote_matches(quote: str, line_text: str) -> bool:
    nq, nl = normalize(quote), normalize(line_text)
    if not nq or not nl:
        return False
    if nq in nl:
        return True
    # Ett citat som är längre än raden kan inte stå på raden (partial_ratio
    # skulle annars mäta raden mot citatet och släppa igenom tillagd text).
    if len(nq) > len(nl):
        return False
    return fuzz.partial_ratio(nq, nl) >= QUOTE_THRESHOLD


def validate_evidence(evidence: list[EvidenceLLM], lines_by_id: dict[int, Line]) -> list[Evidence]:
    valid: list[Evidence] = []
    seen: set[tuple[int, str]] = set()
    for ev in evidence:
        line = lines_by_id.get(ev.line_id)
        if line is None or not quote_matches(ev.quote, line.text):
            continue
        key = (ev.line_id, normalize(ev.quote))
        if key in seen:
            continue
        seen.add(key)
        valid.append(Evidence(line_id=ev.line_id, quote=ev.quote.strip()))
    return valid


def find_span(note_text: str, quote: str, start_at: int = 0) -> NoteSpan | None:
    """Hittar citatets position i anteckningen: exakt, sedan skiftlägesokänsligt, sist fuzzy."""
    quote = quote.strip()
    if not quote:
        return None
    for haystack, needle in ((note_text, quote), (note_text.lower(), quote.lower())):
        for offset in (start_at, 0):
            idx = haystack.find(needle, offset)
            if idx != -1:
                return NoteSpan(start=idx, end=idx + len(needle))
    if len(quote) < 4:
        return None
    alignment = fuzz.partial_ratio_alignment(quote, note_text, processor=str.lower)
    if alignment is None or alignment.score < SPAN_THRESHOLD:
        return None
    return NoteSpan(start=alignment.dest_start, end=alignment.dest_end)


def check_claims(claims_llm: ClaimsLLM, lines: list[Line], note_text: str) -> list[Claim]:
    lines_by_id = {line.line_id: line for line in lines}
    claims: list[Claim] = []
    cursor = 0
    raw_claims = [c for c in claims_llm.claims if c.note_quote.strip() and c.text.strip()][:MAX_CLAIMS]
    for i, raw in enumerate(raw_claims, start=1):
        evidence = validate_evidence(raw.evidence, lines_by_id)
        status = raw.status
        receipt_failed = False
        correction = raw.correction.strip()
        if status == "unsupported":
            evidence = []
        elif not evidence:
            status = "unsupported"
            receipt_failed = True
            correction = ""
        if status in ("supported", "inferred"):
            correction = ""
        span = find_span(note_text, raw.note_quote, cursor)
        if span is not None:
            cursor = span.end
        claims.append(
            Claim(
                claim_id=f"C{i}",
                text=raw.text.strip()[:MAX_TEXT],
                note_quote=raw.note_quote,
                note_span=span,
                status=status,
                evidence=evidence,
                explanation=raw.explanation.strip()[:MAX_TEXT],
                correction=correction[:MAX_TEXT],
                receipt_failed=receipt_failed,
            )
        )
    return claims


def check_coverage(coverage_llm: CoverageLLM, lines: list[Line], claims: list[Claim]) -> tuple[list[Addition], list[FollowUp]]:
    lines_by_id = {line.line_id: line for line in lines}
    documented = [normalize(c.text) for c in claims if c.status != "unsupported"]
    additions: list[Addition] = []
    for raw in coverage_llm.additions:
        if len(additions) >= MAX_ADDITIONS or not raw.text.strip():
            continue
        evidence = validate_evidence(raw.evidence, lines_by_id)
        if not evidence:
            continue  # ett tillägg utan äkta kvitto visas aldrig
        text_norm = normalize(raw.text)
        if any(fuzz.token_sort_ratio(text_norm, d) >= DUPLICATE_THRESHOLD for d in documented):
            continue
        additions.append(
            Addition(
                addition_id=f"A{len(additions) + 1}",
                text=raw.text.strip()[:MAX_TEXT],
                why=raw.why.strip()[:MAX_TEXT],
                priority=raw.priority,
                evidence=evidence,
            )
        )
    follow_ups = [
        FollowUp(category=f.category.strip()[:60] or "Follow-up", question=f.question.strip()[:MAX_TEXT], why=f.why.strip()[:MAX_TEXT])
        for f in coverage_llm.follow_up_questions
        if f.question.strip()
    ][:MAX_FOLLOW_UPS]
    return additions, follow_ups


def summarize(claims: list[Claim]) -> Summary:
    counts = Counter(c.status for c in claims)
    total = len(claims)
    with_receipt = counts["supported"] + counts["inferred"]
    return Summary(
        total=total,
        supported=counts["supported"],
        inferred=counts["inferred"],
        contradicted=counts["contradicted"],
        unsupported=counts["unsupported"],
        with_receipt_pct=round(100 * with_receipt / total, 1) if total else 0.0,
    )


# ---------------------------------------------------------------- andra blick, besökskontroll, frågor


def _clip(text: str, limit: int = MAX_TEXT) -> str:
    return text.strip()[:limit]


def check_flags(flags: list[DecisionFlagLLM], lines: list[Line], prefix: str = "F") -> list[DecisionFlag]:
    """Andra blicken på beslut. En flagga får sakna kvitto (ofta gäller den något
    som aldrig togs upp), men de kvitton som anges måste vara äkta."""
    lines_by_id = {line.line_id: line for line in lines}
    out: list[DecisionFlag] = []
    for raw in flags:
        if len(out) >= MAX_FLAGS or not raw.title.strip() or not raw.concern.strip():
            continue
        out.append(DecisionFlag(
            flag_id=f"{prefix}{len(out) + 1}",
            category=raw.category,
            severity=raw.severity,
            title=_clip(raw.title, 120),
            concern=_clip(raw.concern),
            consider=_clip(raw.consider),
            evidence=validate_evidence(raw.evidence, lines_by_id),
        ))
    return out


def check_missed(items: list[MissedItemLLM], lines: list[Line]) -> list[MissedItem]:
    lines_by_id = {line.line_id: line for line in lines}
    out: list[MissedItem] = []
    for raw in items:
        if len(out) >= MAX_MISSED or not raw.question.strip():
            continue
        out.append(MissedItem(
            item_id=f"M{len(out) + 1}",
            category=_clip(raw.category, 60) or "Follow-up",
            question=_clip(raw.question),
            why=_clip(raw.why),
            evidence=validate_evidence(raw.evidence, lines_by_id),
        ))
    return out


def check_answer(answer: AskLLM, record: list[Line]) -> tuple[AskLLM, list[Finding], list[str]]:
    """Ett svar på en fråga till journalen får bara peka på det som faktiskt står
    där. Fynd utan äkta kvitto tas bort; finns inga kvar kan svaret inte påstå
    att det finns belägg."""
    lines_by_id = {line.line_id: line for line in record}
    findings: list[Finding] = []
    for raw in answer.findings:
        if len(findings) >= MAX_FINDINGS or not raw.text.strip():
            continue
        evidence = validate_evidence(raw.evidence, lines_by_id)
        if not evidence:
            continue
        findings.append(Finding(finding_id=f"E{len(findings) + 1}", stance=raw.stance, text=_clip(raw.text), evidence=evidence))
    warnings: list[str] = []
    verdict = answer.verdict
    if verdict != "no_evidence_found" and not any(f.stance == "supports" for f in findings):
        verdict = "no_evidence_found"
        warnings.append("The answer pointed to evidence that could not be found in the record, so it is shown as no evidence found.")
    where = [_clip(w, 200) for w in answer.where_to_look if w.strip()][:MAX_WHERE]
    cleaned = AskLLM(verdict=verdict, summary=_clip(answer.summary), findings=[], where_to_look=where)
    return cleaned, findings, warnings
