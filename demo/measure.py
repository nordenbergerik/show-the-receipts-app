"""Mäter tid, tokens, kostnad och träffsäkerhet för AI-stegen mot det riktiga
Claude-API:t, på ett exempelfall och med tom cache. Skriver bara ut siffror:
aldrig nyckeln, aldrig patienttext eller modellens svar.

  .venv/Scripts/python -m demo.measure [fall]            # hela demon
  .venv/Scripts/python -m demo.measure [fall] --audits   # bara granskningarna

--audits granskar den korrekta journalen och journalen med fel och räknar hur
många av de inplanterade felen som hittas och hur många falsklarm det blir.
Effort styrs med CLAUDE_EFFORT och CLAUDE_EFFORT_<STEG> (se .env.example).
Kostar riktiga pengar.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

os.environ["DEMO_REPLAY"] = "0"  # bara riktiga anrop
os.environ["AUTH_PASSWORD"] = ""  # skriptet går direkt mot API:t, utan inloggning i webbläsaren

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app, get_cache, get_llm  # noqa: E402
from backend.cache import AuditCache  # noqa: E402
from backend.config import ROOT, get_settings  # noqa: E402
from backend.llm import ClaudeLLM  # noqa: E402
from demo.record import load_case, normalize_transcript, note_text  # noqa: E402

OUT = ROOT / "demo" / "measurements"


def quality(data: dict, planted: list[str]) -> dict:
    """Utfallet av en granskning: antal problem och hur många inplanterade fel som hittades."""
    flagged = [c for c in data["claims"] if c["status"] in ("contradicted", "unsupported")]
    found = [p for p in planted if any(p.lower() in (c["note_quote"] + " " + c["text"]).lower() for c in flagged)]
    return {"statements": len(data["claims"]), "problems": len(flagged), "planted_found": len(found),
            "planted_total": len(planted), "missing": len(data["suggested_additions"]), "flags": len(data["decision_flags"])}


def main(case: str = "chest-pain", audits_only: bool = False) -> int:
    settings = get_settings()
    folder, _, config = load_case(case)
    transcript = normalize_transcript((folder / "transcript.txt").read_text(encoding="utf-8"))
    planted = config.get("planted_errors", {})
    wrong = next((n for n in config["notes"] if "wrong" in n), config["notes"][0])
    correct = next((n for n in config["notes"] if "correct" in n), None)
    efforts = {task: settings.effort_for(task) for task in ("ClaimsLLM", "CoverageLLM", "DecisionsLLM")}
    rows: list[dict] = []
    tmp = tempfile.TemporaryDirectory()
    app.dependency_overrides[get_llm] = lambda: ClaudeLLM(settings)
    app.dependency_overrides[get_cache] = lambda: AuditCache(Path(tmp.name))

    print(f"Model {settings.model}, default effort {settings.effort}, note check efforts {efforts}.")
    print("Live calls, this takes a few minutes.\n")
    with TestClient(app) as c:
        def step(name: str, path: str, body: dict, planted_errors: list[str] | None = None) -> dict | None:
            started = time.perf_counter()
            r = c.post(path, json={"transcript_text": transcript, **body})
            wall = time.perf_counter() - started
            if r.status_code != 200:
                print(f"{name:<36} FAILED ({r.status_code}): {r.json().get('detail', '')[:120]}")
                return None
            data = r.json()
            u = data["meta"].get("usage") or {}
            row = {
                "step": name.strip(), "seconds": round(wall, 1), "calls": len(u.get("calls", [])),
                "input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                "cache_read": u.get("cache_read_tokens", 0), "cache_write": u.get("cache_write_tokens", 0),
                "cost_usd": u.get("cost_usd", 0.0), "source": u.get("source", "?"),
                "per_call": [{k: call[k] for k in ("task", "model", "seconds", "input_tokens", "output_tokens", "cost_usd", "source")}
                             for call in u.get("calls", [])],
            }
            extra = ""
            if path == "/api/audit":
                q = row["quality"] = quality(data, planted_errors or [])
                extra = f"  {q['problems']} problems"
                if q["planted_total"]:
                    extra += f", planted errors found {q['planted_found']}/{q['planted_total']}"
            rows.append(row)
            print(f"{name:<36} {row['seconds']:>6.1f} s  ${row['cost_usd']:.3f}  ({row['calls']} calls){extra}")
            return data

        if audits_only:
            if correct:
                step("Note check, correct note", "/api/audit", {"note_text": note_text(folder, correct)}, planted.get(correct, []))
            step("Note check, note with errors", "/api/audit", {"note_text": note_text(folder, wrong)}, planted.get(wrong, []))
        else:
            step("Visit check", "/api/visit-check", {})
            draft = step("Draft note (SOAP)", "/api/draft-note", {"note_format": "soap"})
            if draft:
                step("Note check of the draft", "/api/audit", {"note_text": draft["note_text"]})
            step("Note check, note with errors", "/api/audit", {"note_text": note_text(folder, wrong)}, planted.get(wrong, []))
            step("Ask one question", "/api/ask", {"question": config["questions"][0], "note_text": note_text(folder, wrong)})
    app.dependency_overrides.clear()
    tmp.cleanup()

    total = round(sum(r["cost_usd"] for r in rows), 4)
    print(f"\nTotal: {sum(r['seconds'] for r in rows):.1f} s, ${total:.3f}")
    OUT.mkdir(parents=True, exist_ok=True)
    label = f"{settings.effort}-claims-{efforts['ClaimsLLM']}{'-audits' if audits_only else ''}"
    path = OUT / f"{datetime.now():%Y-%m-%d-%H%M%S}-{settings.model}-{label}.json"
    path.write_text(json.dumps({"model": settings.model, "effort": settings.effort, "efforts": efforts, "case": case,
                                "steps": rows, "total_cost_usd": total}, indent=1), encoding="utf-8")
    print(f"Saved {path.relative_to(ROOT)}")
    return 0 if len(rows) == (2 if audits_only else 5) else 1


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else "chest-pain", audits_only="--audits" in sys.argv))
