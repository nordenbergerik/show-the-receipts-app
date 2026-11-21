"""Kontrakt: dels vad LLM:en ska returnera (*LLM), dels vad API:t returnerar."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from backend.config import MAX_NOTE_CHARS, MAX_TRANSCRIPT_CHARS
from backend.transcript import Line

Status = Literal["supported", "inferred", "contradicted", "unsupported"]
Priority = Literal["high", "medium", "low"]
Variant = Literal["clean", "asr", "asrcorr", "humantrans"]


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
    question: str
    why: str


class CoverageLLM(BaseModel):
    additions: list[AdditionLLM]
    follow_up_questions: list[FollowUpLLM]


# ---------------------------------------------------------------- API


class AuditRequest(BaseModel):
    encounter_id: str | None = Field(default=None, max_length=32)
    variant: Variant = "clean"
    transcript_text: str | None = Field(default=None, max_length=MAX_TRANSCRIPT_CHARS)
    note_text: str = Field(min_length=1, max_length=MAX_NOTE_CHARS)

    @model_validator(mode="after")
    def _one_source(self) -> "AuditRequest":
        if bool(self.encounter_id) == bool(self.transcript_text):
            raise ValueError("Provide either encounter_id or transcript_text")
        return self


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
    question: str
    why: str


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


class AuditResult(BaseModel):
    encounter_id: str | None
    variant: str | None
    transcript: list[Line]
    note_text: str
    claims: list[Claim]
    suggested_additions: list[Addition]
    follow_up_questions: list[FollowUp]
    summary: Summary
    meta: AuditMeta
