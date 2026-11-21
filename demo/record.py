"""Inspelade AI-svar för exempelfallen i demo/cases/.

Exporterar exakt de förfrågningar appen skickar (samma byggfunktioner som
backend använder) som uppgiftsfiler. En agent besvarar dem i API:ts ställe och
skriver svaren till demo/recordings/<nyckel>.json. Appen spelar sedan upp dem
när den körs utan API-nyckel.

  .venv/Scripts/python -m demo.record export <fall> <uppgiftsmapp>
  .venv/Scripts/python -m demo.record export-draft <fall> <uppgiftsmapp>   # efter att utkastet spelats in
  .venv/Scripts/python -m demo.record check <fall>
  .venv/Scripts/python -m demo.record prune <fall>     # tar bort inaktuella inspelningar
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from backend.config import ROOT
from backend.prompts import ASK_SYSTEM, CLAIMS_SYSTEM, COVERAGE_SYSTEM, DECISIONS_SYSTEM, DRAFT_SYSTEM, VISIT_CHECK_SYSTEM
from backend.replay import ReplayLLM, replay_key
from backend.schemas import AskLLM, ClaimsLLM, CoverageLLM, DecisionsLLM, DraftNoteLLM, VisitCheckLLM
from backend.transcript import index_transcript
from backend.visit import ask_content, audit_content, draft_content, visit_check_content

CASES = ROOT / "demo" / "cases"
RECORDINGS = ROOT / "demo" / "recordings"
REPLAY = ReplayLLM(RECORDINGS)
FORMATS = ("soap", "plain")
AUDIT_TASKS = [("ClaimsLLM", ClaimsLLM, CLAIMS_SYSTEM), ("CoverageLLM", CoverageLLM, COVERAGE_SYSTEM), ("DecisionsLLM", DecisionsLLM, DECISIONS_SYSTEM)]


def normalize_transcript(text: str) -> str:
    """Samma normalisering som webbläsaren gör vid uppladdning (frontend/app.js)."""
    lines = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"^\s*(doctor|dr\.?|physician|clinician)\s*:\s*", "[doctor] ", line, flags=re.I)
        line = re.sub(r"^\s*(patient|pt)\s*:\s*", "[patient] ", line, flags=re.I)
        lines.append(line)
    return "\n".join(lines).strip()


def load_case(name: str) -> tuple[Path, list, dict]:
    folder = CASES / name
    lines = index_transcript(normalize_transcript((folder / "transcript.txt").read_text(encoding="utf-8")))
    config = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    return folder, lines, config


def note_text(folder: Path, filename: str) -> str:
    return (folder / filename).read_text(encoding="utf-8").replace("\r\n", "\n").strip()


def user_message(content: list[dict]) -> str:
    return "\n\n".join(block["text"] for block in content)


def task_file(title: str, content: list[dict], tasks: list[tuple[str, type, str]]) -> str:
    out = [f"# {title}", "",
           "You are standing in for the Claude API. The app sends the user message below once per task, each time with that",
           "task's system prompt, and expects JSON that validates against that task's schema. Answer each task independently,",
           "following its system prompt to the letter. Judge only from the user message; read no other files.",
           "", "Rules a deterministic checker enforces afterwards: every quote must be copied verbatim from the line it cites",
           "(keep the line's own spelling and spacing, for example \"do n't\" and \" , \"), never spanning two lines; a note_quote",
           "must be copied character for character from the note.", ""]
    for i, (fmt, model, system) in enumerate(tasks, start=1):
        path = REPLAY.path_for(fmt, content)
        out += [f"## Task {i}: {fmt}", "", "### System prompt", "```text", system, "```", "### Response JSON schema", "```json",
                json.dumps(model.model_json_schema(), indent=1), "```",
                f"### Write the answer to `{path.as_posix()}`",
                f'with exactly this shape: {{"format": "{fmt}", "source": "agent", "response": <the response object>}}', ""]
    out += ["## User message (identical for every task)", "```text", user_message(content), "```"]
    return "\n".join(out)


def export(case: str, out_dir: Path) -> None:
    folder, lines, config = load_case(case)
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs = {
        "visit-check": task_file("Visit check", visit_check_content(lines, ""), [("VisitCheckLLM", VisitCheckLLM, VISIT_CHECK_SYSTEM)]),
        "drafts": "\n\n".join(
            task_file(f"Note draft, format {fmt}", draft_content(lines, "", [], fmt), [("DraftNoteLLM", DraftNoteLLM, DRAFT_SYSTEM)])
            for fmt in FORMATS
        ),
    }
    for filename in config["notes"]:
        jobs[f"audit-{Path(filename).stem}"] = task_file(f"Review of {filename}", audit_content(lines, "", note_text(folder, filename)), AUDIT_TASKS)
    asks = []
    for question in config["questions"]:
        content, _ = ask_content(lines, "", "", question)
        asks.append(task_file(f"Question: {question}", content, [("AskLLM", AskLLM, ASK_SYSTEM)]))
    jobs["ask"] = "\n\n".join(asks)
    for name, text in jobs.items():
        (out_dir / f"{case}-{name}.md").write_text(text, encoding="utf-8")
        print(out_dir / f"{case}-{name}.md")


def recorded_draft(lines: list, fmt: str) -> str:
    path = REPLAY.path_for("DraftNoteLLM", draft_content(lines, "", [], fmt))
    return json.loads(path.read_text(encoding="utf-8"))["response"]["note_text"].replace("\r\n", "\n").strip()


def export_draft(case: str, out_dir: Path) -> None:
    _, lines, _ = load_case(case)
    out_dir.mkdir(parents=True, exist_ok=True)
    for fmt in FORMATS:
        path = out_dir / f"{case}-audit-draft-{fmt}.md"
        path.write_text(task_file(f"Review of the drafted note ({fmt})", audit_content(lines, "", recorded_draft(lines, fmt)), AUDIT_TASKS), encoding="utf-8")
        print(path)


def expected_keys(case: str) -> set[str]:
    """Alla inspelningar som fallet behöver; allt annat i demo/recordings är inaktuellt."""
    folder, lines, config = load_case(case)
    keys = {replay_key("VisitCheckLLM", visit_check_content(lines, ""))}
    notes = [note_text(folder, f) for f in config["notes"]]
    for fmt in FORMATS:
        keys.add(replay_key("DraftNoteLLM", draft_content(lines, "", [], fmt)))
        try:
            notes.append(recorded_draft(lines, fmt))
        except FileNotFoundError:
            pass
    for text in notes:
        for fmt_name, _, _ in AUDIT_TASKS:
            keys.add(replay_key(fmt_name, audit_content(lines, "", text)))
    for question in config["questions"]:
        keys.add(replay_key("AskLLM", ask_content(lines, "", "", question)[0]))
    return keys


def prune(case: str) -> None:
    keep = expected_keys(case)
    for path in sorted(RECORDINGS.glob("*.json")):
        if path.stem not in keep:
            path.unlink()
            print(f"removed stale {path.name}")


def check(case: str) -> int:
    """Kör alla inspelningar för fallet genom API:t med uppspelning och kvittokontroll."""
    import os
    os.environ["DEMO_REPLAY"] = "1"
    from fastapi.testclient import TestClient

    from backend.app import app, get_llm
    app.dependency_overrides[get_llm] = lambda: REPLAY
    folder, lines, config = load_case(case)
    transcript = normalize_transcript((folder / "transcript.txt").read_text(encoding="utf-8"))
    ok = True
    with TestClient(app) as c:
        def post(path, body):
            nonlocal ok
            r = c.post(path, json={"transcript_text": transcript, **body})
            if r.status_code != 200:
                ok = False
                print(f"  ! {path}: {r.status_code} {r.text[:200]}")
                return None
            return r.json()
        vc = post("/api/visit-check", {})
        if vc:
            print(f"visit check: {len(vc['missed'])} missed, {len(vc['decision_flags'])} decision flags")
        notes = {f: note_text(folder, f) for f in config["notes"]}
        try:
            for fmt in FORMATS:
                notes[f"(draft {fmt})"] = recorded_draft(lines, fmt)
        except FileNotFoundError:
            print("  ! no recorded draft yet")
            ok = False
        for name, text in notes.items():
            a = post("/api/audit", {"note_text": text})
            if a:
                s = a["summary"]
                failed = sum(1 for cl in a["claims"] if cl["receipt_failed"] or cl["note_span"] is None)
                print(f"audit {name}: {s['total']} statements, {s['contradicted']} contradicted, {s['unsupported']} unsupported, "
                      f"{len(a['suggested_additions'])} missing, {len(a['decision_flags'])} flags, {failed} receipt/span problems, warnings={a['warnings']}")
        for q in config["questions"]:
            a = post("/api/ask", {"question": q, "note_text": notes.get(config["notes"][0], "")})
            if a:
                print(f"ask '{q}': {a['verdict']}, {len(a['findings'])} findings, warnings={a['warnings']}")
    app.dependency_overrides.clear()
    return 0 if ok else 1


if __name__ == "__main__":
    command, case, *rest = sys.argv[1:]
    if command == "export":
        export(case, Path(rest[0]))
    elif command == "export-draft":
        export_draft(case, Path(rest[0]))
    elif command == "prune":
        prune(case)
    elif command == "check":
        sys.exit(check(case))
    else:
        raise SystemExit(__doc__)
