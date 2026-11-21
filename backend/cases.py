"""Exempelfallen i demo/cases/: föreslagna frågor till journalen per samtal och
fallen själva, så att de kan startas med ett klick i appen."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from backend.config import ROOT
from backend.transcript import Line, index_transcript, render_for_prompt

CASES_DIR = ROOT / "demo" / "cases"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n").strip()


def _normalize(text: str) -> str:
    """Samma normalisering som webbläsaren gör vid uppladdning."""
    lines = []
    for line in text.replace("\r", "").split("\n"):
        line = re.sub(r"^\s*(doctor|dr\.?|physician|clinician)\s*:\s*", "[doctor] ", line, flags=re.I)
        line = re.sub(r"^\s*(patient|pt)\s*:\s*", "[patient] ", line, flags=re.I)
        lines.append(line)
    return "\n".join(lines).strip()


def transcript_fingerprint(lines: list[Line]) -> str:
    return hashlib.sha256(render_for_prompt(lines).encode("utf-8")).hexdigest()[:24]


@lru_cache(maxsize=1)
def _questions_by_transcript() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for config_path in sorted(CASES_DIR.glob("*/case.json")):
        transcript_path = config_path.parent / "transcript.txt"
        if not transcript_path.exists():
            continue
        lines = index_transcript(_normalize(_read(transcript_path)))
        config = json.loads(config_path.read_text(encoding="utf-8"))
        out[transcript_fingerprint(lines)] = list(config.get("questions", []))
    return out


def suggested_questions(lines: list[Line]) -> list[str]:
    return _questions_by_transcript().get(transcript_fingerprint(lines), [])


@lru_cache(maxsize=1)
def example_cases() -> list[dict]:
    cases = []
    for config_path in sorted(CASES_DIR.glob("*/case.json")):
        folder = config_path.parent
        config = json.loads(config_path.read_text(encoding="utf-8"))
        cases.append({
            "id": folder.name,
            "title": config.get("title", folder.name),
            "patient": config.get("patient", ""),
            "reason": config.get("reason", ""),
            "transcript": _read(folder / "transcript.txt"),
            "notes": {name: _read(folder / name) for name in config.get("notes", [])},
        })
    return cases
