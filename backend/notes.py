"""Anteckningar som kan väljas per besök: förberedda demoanteckningar
(demo/notes.json), expertjournalen ur datasetet och en tom egen anteckning."""
from __future__ import annotations

import json
from functools import lru_cache

from backend.config import ROOT
from backend.data import Encounter

REFERENCE = {
    "id": "reference",
    "label": "Expert note (ACI-Bench)",
    "description": "The full reference note that ships with the dataset, written and reviewed by experts.",
}
OWN = {"id": "own", "label": "Your own note", "description": "Type or paste any note to check it against this visit.", "note_text": ""}


@lru_cache(maxsize=1)
def demo_notes() -> dict[str, list[dict]]:
    path = ROOT / "demo" / "notes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def notes_for(enc: Encounter) -> list[dict]:
    """Demoanteckningarna först, sedan expertjournalen och en tom egen anteckning.

    En demopost med id "reference" utan text får expertjournalens text, så att
    den kan bära ett exempel ("sample") utan att texten dupliceras.
    """
    notes: list[dict] = []
    has_reference = False
    for note in demo_notes().get(enc.encounter_id, []):
        if note["id"] == "reference":
            has_reference = True
            note = {**REFERENCE, **note, "note_text": note.get("note_text") or enc.reference_note}
        notes.append(note)
    if not has_reference:
        notes.append({**REFERENCE, "note_text": enc.reference_note})
    notes.append(dict(OWN))
    return notes
