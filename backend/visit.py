"""Gemensamt för alla AI-steg: slå upp besöket och bygg exakt det innehåll som
skickas till modellen. Samma byggfunktioner används av inspelningsskripten, så
att inspelade svar alltid matchar det appen faktiskt skickar."""
from __future__ import annotations

from typing import Any

from backend.data import DataStore
from backend.prompts import accepted_block, format_block, note_block, numbered_note_block, question_block, transcript_block
from backend.schemas import VisitSource
from backend.transcript import Line, index_transcript, render_for_prompt


class VisitError(Exception):
    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


def resolve_visit(src: VisitSource, store: DataStore) -> tuple[list[Line], str]:
    """Returnerar (indexerat samtal, huvudbesvär)."""
    if src.transcript_text:
        dialogue, chief_complaint = src.transcript_text, src.chief_complaint
    else:
        enc = store.get(src.encounter_id or "")
        if enc is None:
            raise VisitError(f"Unknown visit: {src.encounter_id}", 404)
        dialogue = enc.variants.get(src.variant)
        if dialogue is None:
            raise VisitError(f"{enc.encounter_id} has no '{src.variant}' transcript", 404)
        chief_complaint = enc.chief_complaint
    lines = index_transcript(dialogue)
    if not lines:
        raise VisitError("The transcript is empty.", 400)
    return lines, chief_complaint


def _transcript_part(lines: list[Line], chief_complaint: str) -> dict[str, Any]:
    # Samtalet ligger först och cachas (prompt caching): flera anrop om samma
    # besök återanvänder prefixet.
    return {"type": "text", "text": transcript_block(render_for_prompt(lines), chief_complaint), "cache_control": {"type": "ephemeral"}}


def audit_content(lines: list[Line], chief_complaint: str, note_text: str) -> list[dict[str, Any]]:
    return [_transcript_part(lines, chief_complaint), {"type": "text", "text": note_block(note_text)}]


def visit_check_content(lines: list[Line], chief_complaint: str) -> list[dict[str, Any]]:
    return [_transcript_part(lines, chief_complaint)]


def draft_content(lines: list[Line], chief_complaint: str, accepted: list[str], note_format: str = "soap") -> list[dict[str, Any]]:
    content = [_transcript_part(lines, chief_complaint), {"type": "text", "text": format_block(note_format)}]
    points = [p.strip() for p in accepted if p.strip()]
    if points:
        content.append({"type": "text", "text": accepted_block(points)})
    return content


def record_lines(lines: list[Line], note_text: str) -> list[Line]:
    """Hela journalen för en fråga: samtalet följt av anteckningens rader."""
    record = list(lines)
    for text in (t.strip() for t in note_text.splitlines()):
        if text:
            record.append(Line(line_id=len(record) + 1, speaker="note", text=text))
    return record


def ask_content(lines: list[Line], chief_complaint: str, note_text: str, question: str) -> tuple[list[dict[str, Any]], list[Line]]:
    record = record_lines(lines, note_text)
    content = [_transcript_part(lines, chief_complaint)]
    note_part = [line for line in record if line.speaker == "note"]
    if note_part:
        content.append({"type": "text", "text": numbered_note_block(render_for_prompt(note_part, start_width=len(str(len(record)))))})
    content.append({"type": "text", "text": question_block(question.strip())})
    return content, record
