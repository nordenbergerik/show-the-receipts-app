"""Jämför två modeller på AI-utkastet och granskningen (Note check) för några
testpersoner, med riktiga API-anrop och tom cache. Kostar riktiga pengar.

  # ett körpass: en modell, en testperson (kör gärna flera parallellt)
  CLAUDE_MODEL=claude-sonnet-5-5 .venv/Scripts/python -m demo.compare_models run 02-donna-headache

  # jämförelsen av allt som körts
  .venv/Scripts/python -m demo.compare_models report

Granskningen görs på testpersonens journal.txt (samma text för båda modellerna),
så att svaren går att jämföra rad för rad. Resultaten hamnar i
demo/measurements/compare/. Skriver aldrig ut nyckeln.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

from rapidfuzz import fuzz

from backend.config import ROOT

OUT = ROOT / "demo" / "measurements" / "compare"
PEOPLE = ROOT / "testpersoner"
MODEL_NAMES = {"claude-fable-5-2": "Fable 5.2", "claude-sonnet-5-5": "Sonnet 5.5"}
SAME_QUOTE = 80  # två flaggor gäller samma ställe i journalen


def run(person: str) -> int:
    os.environ["DEMO_REPLAY"] = "0"
    os.environ["AUTH_PASSWORD"] = ""
    from fastapi.testclient import TestClient

    from backend.app import app, get_cache, get_llm
    from backend.cache import AuditCache
    from backend.config import get_settings
    from backend.llm import ClaudeLLM
    from demo.record import normalize_transcript

    settings = get_settings()
    folder = PEOPLE / person
    transcript = normalize_transcript((folder / "transcript.txt").read_text(encoding="utf-8"))
    journal = (folder / "journal.txt").read_text(encoding="utf-8")
    tmp = tempfile.TemporaryDirectory()
    app.dependency_overrides[get_llm] = lambda: ClaudeLLM(settings)
    app.dependency_overrides[get_cache] = lambda: AuditCache(Path(tmp.name))
    result: dict = {"model": settings.model, "effort": settings.effort, "person": person}
    with TestClient(app) as c:
        for step, path, body in (("draft", "/api/draft-note", {"note_format": "soap"}),
                                 ("check", "/api/audit", {"note_text": journal})):
            started = time.perf_counter()
            r = c.post(path, json={"transcript_text": transcript, **body})
            seconds = round(time.perf_counter() - started, 1)
            if r.status_code != 200:
                print(f"{person} {step} FAILED ({r.status_code})")
                result[step] = {"failed": r.status_code}
                continue
            data = r.json()
            u = data["meta"].get("usage") or {}
            row = {"seconds": seconds, "cost_usd": u.get("cost_usd", 0.0), "output_tokens": u.get("output_tokens", 0)}
            if step == "draft":
                row["note_text"] = data["note_text"]
            else:
                row["claims"] = [{k: cl[k] for k in ("note_quote", "text", "status", "correction")} for cl in data["claims"]]
                row["additions"] = [a["text"] for a in data["suggested_additions"]]
                row["follow_ups"] = [f.get("title") or f.get("question", "") for f in data.get("follow_up_questions", [])]
                row["flags"] = [f["title"] for f in data["decision_flags"]]
                row["warnings"] = data.get("warnings", [])
            result[step] = row
            print(f"{person:<45} {MODEL_NAMES.get(settings.model, settings.model):<11} {step:<6} {seconds:>6.1f} s  ${row['cost_usd']:.3f}")
    app.dependency_overrides.clear()
    tmp.cleanup()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{settings.model}--{person}.json").write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0 if all("failed" not in result.get(s, {"failed": 1}) for s in ("draft", "check")) else 1


# ------------------------------------------------------------------ jämförelse

SECTION = re.compile(r"^\s*(subjective|objective|assessment|plan|assessment and plan)\b", re.I | re.M)
NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:mg|mcg|g|ml|mmhg|%|bpm|days?|weeks?|months?|years?|hours?|x)?\b", re.I)


def flagged(check: dict) -> list[dict]:
    return [c for c in check["claims"] if c["status"] in ("contradicted", "unsupported")]


def matched(a: list[str], b: list[str]) -> int:
    """Hur många i a som har en motsvarighet i b (samma ställe i journalen)."""
    return sum(any(fuzz.partial_ratio(x.lower(), y.lower()) >= SAME_QUOTE for y in b) for x in a)


def numbers(text: str) -> set[str]:
    return {re.sub(r"\s+", " ", m.lower().strip()) for m in NUMBER.findall(text)}


def report() -> int:
    runs: dict[str, dict[str, dict]] = {}
    for f in sorted(OUT.glob("*--*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        runs.setdefault(d["person"], {})[d["model"]] = d
    models = ["claude-fable-5-2", "claude-sonnet-5-5"]
    md = ["# Fable 5.2 vs Sonnet 5.5: utkast och Note check", "",
          "Samma samtal och samma journal (`journal.txt`) för båda modellerna, effort high, tom cache.", ""]
    totals = {m: {"seconds": 0.0, "cost": 0.0} for m in models}
    for person, by_model in runs.items():
        if not all(m in by_model for m in models):
            continue
        fa, so = (by_model[m] for m in models)
        md += [f"## {person}", "", "| | Fable 5.2 | Sonnet 5.5 |", "|---|---|---|"]
        for step, label in (("draft", "Utkast"), ("check", "Note check")):
            md.append(f"| {label}, tid och kostnad | {fa[step]['seconds']} s · ${fa[step]['cost_usd']:.3f} | "
                      f"{so[step]['seconds']} s · ${so[step]['cost_usd']:.3f} |")
            for m, d in zip(models, (fa, so)):
                totals[m]["seconds"] += d[step]["seconds"]
                totals[m]["cost"] += d[step]["cost_usd"]
        da, ds = fa["draft"]["note_text"], so["draft"]["note_text"]
        md.append(f"| Utkastets längd | {len(da.split())} ord | {len(ds.split())} ord |")
        md.append(f"| SOAP-rubriker | {len(set(s.lower() for s in SECTION.findall(da)))} | {len(set(s.lower() for s in SECTION.findall(ds)))} |")
        ca, cs = fa["check"], so["check"]
        for label, key in (("Påståenden granskade", None), ("Motsägs av samtalet", "contradicted"), ("Saknar stöd", "unsupported")):
            count = (lambda c: len(c["claims"])) if key is None else (lambda c, k=key: sum(x["status"] == k for x in c["claims"]))
            md.append(f"| {label} | {count(ca)} | {count(cs)} |")
        md.append(f"| Saknas i journalen | {len(ca['additions'])} | {len(cs['additions'])} |")
        md.append(f"| Beslut värda en andra titt | {len(ca['flags'])} | {len(cs['flags'])} |")
        qa, qs = [c["note_quote"] for c in flagged(ca)], [c["note_quote"] for c in flagged(cs)]
        both = matched(qa, qs)
        md += ["", f"**Utkasten:** textlikhet {fuzz.token_set_ratio(da.lower(), ds.lower()):.0f}/100. "
               f"Siffror och doser i båda: {len(numbers(da) & numbers(ds))} av {len(numbers(da) | numbers(ds))}.", "",
               f"**Granskningen:** {both} av Fables {len(qa)} flaggor flaggas också av Sonnet; "
               f"{matched(qs, qa)} av Sonnets {len(qs)} flaggas också av Fable.", ""]
        md += ["| Flaggat ställe i journalen | Fable 5.2 | Sonnet 5.5 |", "|---|---|---|"]
        seen: list[str] = []
        for c in flagged(ca) + flagged(cs):
            if any(fuzz.partial_ratio(c["note_quote"].lower(), s.lower()) >= SAME_QUOTE for s in seen):
                continue
            seen.append(c["note_quote"])
            def status(check, q=c["note_quote"]):
                # det påstående som liknar mest, inte det första som är tillräckligt likt
                score, best = max(((fuzz.partial_ratio(q.lower(), x["note_quote"].lower()), x) for x in check["claims"]),
                                  key=lambda t: t[0], default=(0, None))
                return best["status"] if best and score >= SAME_QUOTE else "–"
            md.append(f"| {c['note_quote'][:90].replace('|', '/')} | {status(ca)} | {status(cs)} |")
        md += ["", "<details><summary>Utkasten sida vid sida</summary>", "", "**Fable 5.2**", "", "```", da.strip(), "```", "",
               "**Sonnet 5.5**", "", "```", ds.strip(), "```", "", "</details>", ""]
    md += ["## Totalt", "", "| | Tid | Kostnad |", "|---|---|---|"]
    md += [f"| {MODEL_NAMES[m]} | {t['seconds']:.0f} s | ${t['cost']:.2f} |" for m, t in totals.items()]
    path = OUT.parent / "model-comparison.md"
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    hidden = False
    for line in md:  # utkasten står i filen, inte i terminalen
        hidden = (hidden or line.startswith("<details")) and not line.startswith("</details")
        if not hidden and not line.startswith("</details"):
            print(line)
    print(f"\nSaved {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["run"] and len(sys.argv) == 3:
        sys.exit(run(sys.argv[2]))
    if sys.argv[1:2] == ["report"]:
        sys.exit(report())
    sys.exit(__doc__)
