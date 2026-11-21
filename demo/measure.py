"""Mäter tid, tokens och kostnad för varje AI-steg mot det riktiga Claude-API:t.

Kör båda demoflödena på exempelfallet (hela flödet och tillägget) med en tom
cache, och skriver ut en tabell. Skriptet visar bara siffror: aldrig nyckeln,
aldrig patienttext eller modellens svar.

  .venv/Scripts/python -m demo.measure [fall]
Kostar riktiga pengar (några dollar med Fable 5.2 på high effort).
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


def main(case: str = "chest-pain") -> int:
    settings = get_settings()
    folder, _, config = load_case(case)
    transcript = normalize_transcript((folder / "transcript.txt").read_text(encoding="utf-8"))
    rows: list[dict] = []
    tmp = tempfile.TemporaryDirectory()
    app.dependency_overrides[get_llm] = lambda: ClaudeLLM(settings)
    app.dependency_overrides[get_cache] = lambda: AuditCache(Path(tmp.name))

    print(f"Model {settings.model}, effort {settings.effort}. Running live calls, this takes a few minutes...\n")
    with TestClient(app) as c:
        def step(name: str, path: str, body: dict) -> dict | None:
            started = time.perf_counter()
            r = c.post(path, json={"transcript_text": transcript, **body})
            wall = time.perf_counter() - started
            if r.status_code != 200:
                print(f"{name:<34} FAILED ({r.status_code}): {r.json().get('detail', '')[:120]}")
                return None
            data = r.json()
            u = data["meta"].get("usage") or {}
            row = {
                "step": name, "seconds": round(wall, 1), "calls": len(u.get("calls", [])),
                "input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                "cache_read": u.get("cache_read_tokens", 0), "cache_write": u.get("cache_write_tokens", 0),
                "cost_usd": u.get("cost_usd", 0.0), "source": u.get("source", "?"),
                "per_call": [{k: call[k] for k in ("task", "model", "seconds", "input_tokens", "output_tokens", "cost_usd", "source")}
                             for call in u.get("calls", [])],
            }
            rows.append(row)
            print(f"{name:<34} {row['seconds']:>6.1f} s  ${row['cost_usd']:.3f}  ({row['calls']} calls, {row['source']})")
            return data

        print("Full flow")
        step("  Visit check", "/api/visit-check", {})
        draft = step("  Draft note (SOAP)", "/api/draft-note", {"note_format": "soap"})
        if draft:
            step("  Note check of the draft", "/api/audit", {"note_text": draft["note_text"]})
        print("Add-on")
        wrong = next((n for n in config["notes"] if "wrong" in n), config["notes"][0])
        step("  Note check of a note with errors", "/api/audit", {"note_text": note_text(folder, wrong)})
        print("Ask about this patient")
        step("  One question", "/api/ask", {"question": config["questions"][0], "note_text": note_text(folder, wrong)})
    app.dependency_overrides.clear()
    tmp.cleanup()

    print("\n{:<34} {:>7} {:>8} {:>8} {:>10} {:>9}".format("Step", "Time", "Input", "Output", "Cache read", "Cost"))
    for r in rows:
        print("{:<34} {:>6.1f}s {:>8} {:>8} {:>10} {:>8.3f}$".format(r["step"].strip(), r["seconds"], r["input"], r["output"], r["cache_read"], r["cost_usd"]))
    total = sum(r["cost_usd"] for r in rows)
    print(f"{'Total':<34} {sum(r['seconds'] for r in rows):>6.1f}s {'':>8} {'':>8} {'':>10} {total:>8.3f}$")

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{datetime.now():%Y-%m-%d-%H%M}-{settings.model}-{settings.effort}.json"
    path.write_text(json.dumps({"model": settings.model, "effort": settings.effort, "case": case, "steps": rows,
                                "total_cost_usd": round(total, 4)}, indent=1), encoding="utf-8")
    print(f"\nSaved {path.relative_to(ROOT)}")
    return 0 if len(rows) == 5 else 1


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
