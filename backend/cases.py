"""Exempelfallen i demo/cases/: föreslagna frågor till journalen per samtal och
filerna som kan laddas upp under New visit."""
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


# ------------------------------------------------------------------ inbyggda demobesök

DEMO_DATE_MS = 1_795_219_200_000  # 21 november 2026, så att listan inte visar dagens datum


def _without_transcript(answer: dict | None) -> dict | None:
    return {k: v for k, v in answer.items() if k != "transcript"} if answer else None


def _review(audit: dict) -> dict:
    """Det patientlistan visar, räknat som i webbläsaren (issues() i app.js)."""
    problems = sum(1 for c in audit["claims"] if c["status"] in ("contradicted", "unsupported"))
    return {"problems": problems, "missing": len(audit["suggested_additions"]), "stale": False}


@lru_cache(maxsize=1)
def demo_visits() -> list[dict]:
    """Inbyggda besök för fall med förberäknade steg (demo/cases/<fall>/stages.json,
    skapad med demo/record_live.py). Samma patient i flera steg, så att demon kan
    hoppa mellan stegen utan att vänta på AI:n. Webbläsaren lägger in dem i sin egen
    lista; därefter fungerar de som vanliga besök."""
    out: list[dict] = []
    for stages_path in sorted(CASES_DIR.glob("*/stages.json")):
        folder = stages_path.parent
        config = json.loads((folder / "case.json").read_text(encoding="utf-8"))
        stages = json.loads(stages_path.read_text(encoding="utf-8"))
        transcript = _normalize(_read(folder / "transcript.txt"))
        lines = stages["visit_check"]["transcript"]
        visit_check = _without_transcript(stages["visit_check"])
        draft = stages["draft"]["note_text"]
        ask = stages["asks"][0] if stages.get("asks") else None
        reason = config.get("reason", "")
        prefix = f"demo-{folder.name}"

        def visit(n: int, label: str, note: str, work: dict | None, review: dict | None = None) -> dict:
            return {
                "visit": {"id": f"{prefix}-{n}", "patient": config.get("patient", "Patient"), "reason": f"{reason} · {label}",
                          "transcript": transcript, "note": note, "created": DEMO_DATE_MS, "demo": True},
                "work": work,
                "review": review,
            }

        base = {"lines": lines, "visitState": {}, "additionState": {}, "flagState": {}, "followState": {}, "draftCost": ""}
        out += [
            visit(1, "Step 1: visit recorded", "", None),
            visit(2, "Step 2: visit checked", "", {**base, "visitCheck": visit_check, "audit": None, "ask": None}),
            visit(3, "Step 3: note drafted", draft, {**base, "visitCheck": visit_check, "audit": None, "ask": None}),
            visit(4, "Step 4: note checked", draft,
                  {**base, "visitCheck": visit_check, "audit": _without_transcript(stages["audit_draft"]), "ask": ask},
                  _review(stages["audit_draft"])),
        ]
        for i, name in enumerate(config.get("notes", []), start=5):
            out.append(visit(i, "The doctor's own note", _read(folder / name), None))
    return out
