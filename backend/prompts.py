"""Promptar ("skills"). Instruktionerna ligger som .md-filer i backend/skills/ så
att de kan läsas och justeras utan att röra koden.

PROMPT_VERSION räknas fram ur filernas innehåll och ingår i cache-nyckeln: en
ändrad .md-fil ger automatiskt nya svar i stället för gamla ur cachen.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def load_skill(name: str) -> str:
    return (SKILLS_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def system_prompt(*names: str) -> str:
    return "\n\n".join(load_skill(n) for n in names)


def _version() -> str:
    h = hashlib.sha256()
    for path in sorted(SKILLS_DIR.glob("*.md")):
        h.update(path.name.encode())
        h.update(path.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10)).encode("utf-8"))
    return h.hexdigest()[:12]


PROMPT_VERSION = _version()
CLAIMS_SYSTEM = system_prompt("common", "check-claims")
COVERAGE_SYSTEM = system_prompt("common", "find-missing")
DECISIONS_SYSTEM = system_prompt("common", "decision-review")
VISIT_CHECK_SYSTEM = system_prompt("common", "visit-check")
DRAFT_SYSTEM = system_prompt("common", "draft-note")
ASK_SYSTEM = system_prompt("common", "ask-record")


def transcript_block(rendered_transcript: str, chief_complaint: str = "") -> str:
    header = f"Chief complaint: {chief_complaint}\n\n" if chief_complaint else ""
    return f"{header}<transcript>\n{rendered_transcript}\n</transcript>"


def note_block(note_text: str) -> str:
    return f"<note>\n{note_text}\n</note>"


def accepted_block(points: list[str]) -> str:
    items = "\n".join(f"- {p}" for p in points)
    return f"<accepted_points>\n{items}\n</accepted_points>"


def numbered_note_block(note_lines: str) -> str:
    """Anteckningen numrerad efter samtalet, så att svar kan citera den."""
    return f"<note>\n{note_lines}\n</note>"


def question_block(question: str) -> str:
    return f"<question>\n{question}\n</question>"
