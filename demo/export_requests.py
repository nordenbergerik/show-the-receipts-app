"""Skriver ut exakt det som appen skickar till Claude-API:t för en anteckning,
så att en agent kan svara i API:ts ställe medan vi saknar API-nyckel.

Kör: .venv/Scripts/python -m demo.export_requests <encounter_id> <note_id> <utfil>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from backend.data import get_store
from backend.notes import notes_for
from backend.prompts import CLAIMS_SYSTEM, COVERAGE_SYSTEM, note_block, transcript_block
from backend.schemas import ClaimsLLM, CoverageLLM
from backend.transcript import index_transcript, render_for_prompt


def build_request(encounter_id: str, note_id: str) -> str:
    enc = get_store().get(encounter_id)
    if enc is None:
        raise SystemExit(f"Okänt besök: {encounter_id}")
    note = next(n for n in notes_for(enc) if n["id"] == note_id)
    user_message = transcript_block(render_for_prompt(index_transcript(enc.variants["clean"])), enc.chief_complaint)
    user_message += "\n\n" + note_block(note["note_text"])
    return "\n".join([
        "# Simulated Claude API request",
        "",
        "The app sends the SAME user message twice, with two different system prompts (task A and task B).",
        "Each response must be JSON that validates against the given schema.",
        "",
        "## Task A: system prompt",
        "```text", CLAIMS_SYSTEM, "```",
        "## Task A: response JSON schema",
        "```json", json.dumps(ClaimsLLM.model_json_schema(), indent=1), "```",
        "",
        "## Task B: system prompt",
        "```text", COVERAGE_SYSTEM, "```",
        "## Task B: response JSON schema",
        "```json", json.dumps(CoverageLLM.model_json_schema(), indent=1), "```",
        "",
        "## User message (identical for task A and task B)",
        "```text", user_message, "```",
    ])


if __name__ == "__main__":
    encounter_id, note_id, out = sys.argv[1:4]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(build_request(encounter_id, note_id), encoding="utf-8")
    print(out)
