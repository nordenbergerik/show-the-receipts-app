"""Radindexering av samtal.

Varje talartur blir en rad med ett stabilt, 1-baserat line_id. Det är dessa
rad-ID:n som kvittona (evidence) pekar på, både i promptar och i UI:t.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

Speaker = Literal["doctor", "patient", "patient_guest", "unknown", "note"]
KNOWN_SPEAKERS: frozenset[str] = frozenset({"doctor", "patient", "patient_guest"})

_SPEAKER_RE = re.compile(r"^\[(?P<speaker>[A-Za-z_]+)\]\s*(?P<text>.*)$")


class Line(BaseModel):
    line_id: int
    speaker: Speaker
    text: str


def index_transcript(dialogue: str) -> list[Line]:
    """Delar ett samtal i rader, en per talartur. Tomma rader hoppas över.

    Rader utan känd talaretikett får speaker="unknown". Talaretiketter i
    ASR-data är opålitliga och får aldrig ensamma avgöra vem som sa något.
    """
    lines: list[Line] = []
    for raw in dialogue.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        match = _SPEAKER_RE.match(raw)
        if match and match["speaker"].lower() in KNOWN_SPEAKERS:
            speaker = match["speaker"].lower()
            text = match["text"].strip()
        else:
            speaker = "unknown"
            text = raw
        if not text:
            continue
        lines.append(Line(line_id=len(lines) + 1, speaker=speaker, text=text))
    return lines


def render_for_prompt(lines: list[Line], start_width: int | None = None) -> str:
    """Formaterar raderna för LLM:en, t.ex. "[07] [patient] it started two days ago"."""
    width = max(2, start_width or len(str(len(lines))))
    return "\n".join(f"[{line.line_id:0{width}d}] [{line.speaker}] {line.text}" for line in lines)
