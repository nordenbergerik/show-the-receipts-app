"""Kontrakt: dels vad LLM:en ska returnera (*LLM), dels vad API:t returnerar."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.config import MAX_NOTE_CHARS, MAX_TRANSCRIPT_CHARS
from backend.transcript import Line
from backend.usage import StepUsage

Status = Literal["supported", "inferred", "contradicted", "unsupported"]
Priority = Literal["high", "medium", "low"]
Variant = Literal["clean", "asr", "asrcorr", "humantrans"]
FlagCategory = Literal["Diagnosis", "Treatment", "Medication", "Tests", "Follow-up"]
Severity = Literal["important", "consider"]
Verdict = Literal["evidence_found", "some_evidence", "no_evidence_found"]
Stance = Literal["supports", "against"]
NoteFormat = Literal["soap", "plain"]


# ---------------------------------------------------------------- LLM-utdata


class EvidenceLLM(BaseModel):
    line_id: int = Field(description="Transcript line number")
    quote: str = Field(description="Short verbatim substring copied from that line")


class ClaimLLM(BaseModel):
    claim_id: str
    note_quote: str = Field(description="Exact span copied from the note that this claim comes from")
    text: str = Field(description="The claim as a short plain sentence, abbreviations expanded")
    status: Status
    evidence: list[EvidenceLLM]
    explanation: str
    correction: str = Field(
        description="For contradicted or unsupported claims: note_quote rewritten to say only what the transcript "
        "supports, keeping any supported part. Empty string only if nothing is supported and it should be removed. "
        "Empty for supported and inferred."
    )


class ClaimsLLM(BaseModel):
    claims: list[ClaimLLM]


class AdditionLLM(BaseModel):
    text: str = Field(description="Sentence ready to insert into the note")
    why: str
    priority: Priority
    evidence: list[EvidenceLLM]


class FollowUpLLM(BaseModel):
    category: str
    title: str = Field(description="Short heading, at most 6 words")
    question: str
    why: str


class CoverageLLM(BaseModel):
    additions: list[AdditionLLM]
    follow_up_questions: list[FollowUpLLM]


class DecisionFlagLLM(BaseModel):
    category: FlagCategory
    severity: Severity
    title: str = Field(description="Short neutral question or phrase, at most 8 words")
    concern: str = Field(description="What in the visit raises the question")
    consider: str = Field(description="What the clinician might consider, phrased as a suggestion")
    evidence: list[EvidenceLLM]


class DecisionsLLM(BaseModel):
    flags: list[DecisionFlagLLM]


class MissedItemLLM(BaseModel):
    category: str
    title: str = Field(description="Short heading, at most 6 words")
    question: str
    why: str
    evidence: list[EvidenceLLM]


class VisitCheckLLM(BaseModel):
    missed: list[MissedItemLLM]
    decision_flags: list[DecisionFlagLLM]


class DraftNoteLLM(BaseModel):
    note_text: str


class FindingLLM(BaseModel):
    stance: Stance
    text: str
    evidence: list[EvidenceLLM]


class AskLLM(BaseModel):
    verdict: Verdict
    summary: str
    findings: list[FindingLLM]
    where_to_look: list[str]


# ---------------------------------------------------------------- API: anrop


class VisitSource(BaseModel):
    """Ett besök: antingen ur datasetet (encounter_id) eller ett eget samtal."""

    encounter_id: str | None = Field(default=None, max_length=32)
    variant: Variant = "clean"
    transcript_text: str | None = Field(default=None, max_length=MAX_TRANSCRIPT_CHARS)
    chief_complaint: str = Field(default="", max_length=200)

    @model_validator(mode="after")
    def _one_source(self) -> "VisitSource":
        if bool(self.encounter_id) == bool(self.transcript_text):
            raise ValueError("Provide either encounter_id or transcript_text")
        return self


class AuditRequest(VisitSource):
    note_text: str = Field(min_length=1, max_length=MAX_NOTE_CHARS)


class VisitCheckRequest(VisitSource):
    pass


class DraftNoteRequest(VisitSource):
    accepted: list[str] = Field(default_factory=list, max_length=12)
    note_format: NoteFormat = "soap"


class AskRequest(VisitSource):
    question: str = Field(min_length=3, max_length=300)
    note_text: str = Field(default="", max_length=MAX_NOTE_CHARS)


# ---------------------------------------------------------------- API: svar


class Evidence(BaseModel):
    line_id: int
    quote: str


class NoteSpan(BaseModel):
    start: int
    end: int


class Claim(BaseModel):
    claim_id: str
    text: str
    note_quote: str
    note_span: NoteSpan | None
    status: Status
    evidence: list[Evidence]
    explanation: str
    correction: str = ""
    receipt_failed: bool = False


class Addition(BaseModel):
    addition_id: str
    text: str
    why: str
    priority: Priority
    evidence: list[Evidence]


class FollowUp(BaseModel):
    category: str
    title: str = ""
    question: str
    why: str


class DecisionFlag(BaseModel):
    flag_id: str
    category: FlagCategory
    severity: Severity
    title: str
    concern: str
    consider: str
    evidence: list[Evidence]


class MissedItem(BaseModel):
    item_id: str
    category: str
    title: str = ""
    question: str
    why: str
    evidence: list[Evidence]


class Finding(BaseModel):
    finding_id: str
    stance: Stance
    text: str
    evidence: list[Evidence]


class Summary(BaseModel):
    total: int
    supported: int
    inferred: int
    contradicted: int
    unsupported: int
    with_receipt_pct: float


class AuditMeta(BaseModel):
    model: str
    prompt_version: str
    cached: bool
    latency_ms: int
    usage: StepUsage | None = None


class AuditResult(BaseModel):
    encounter_id: str | None
    variant: str | None
    transcript: list[Line]
    note_text: str
    claims: list[Claim]
    suggested_additions: list[Addition]
    follow_up_questions: list[FollowUp]
    decision_flags: list[DecisionFlag] = []
    summary: Summary
    warnings: list[str] = []
    meta: AuditMeta


class VisitCheckResult(BaseModel):
    transcript: list[Line]
    missed: list[MissedItem]
    decision_flags: list[DecisionFlag]
    meta: AuditMeta


class DraftNoteResult(BaseModel):
    note_text: str
    meta: AuditMeta


ASK_DISCLAIMER = (
    "This answer is generated by AI and can be wrong. It can miss evidence that is in the record, and "
    "\"no evidence found\" does not mean the condition is ruled out. Use it to narrow your search, then "
    "review the record yourself."
)


class AskResult(BaseModel):
    question: str
    verdict: Verdict
    summary: str
    findings: list[Finding]
    where_to_look: list[str]
    record: list[Line]
    disclaimer: str = ASK_DISCLAIMER
    warnings: list[str] = []
    meta: AuditMeta
