"""Bygger mappen testpersoner/: tio patienter ur ACI-Bench med samtal och journal,
redo att laddas upp under New visit.

  .venv/Scripts/python -m demo.make_testpersoner
"""
from __future__ import annotations

import re
import shutil

from backend.config import ROOT
from backend.data import get_store
from backend.transcript import index_transcript

PEOPLE = [  # (besök i datasetet, namn i appen, besvär)
    ("D2N164", "Elizabeth Moore", "Chest pain on exertion"),
    ("D2N197", "Donna", "Headache"),
    ("D2N146", "Bobby", "Abdominal pain"),
    ("D2N107", "Bryan Brooks", "Low back pain"),
    ("D2N122", "Kyle", "Cough"),
    ("D2N149", "Dennis", "Shortness of breath with hemoptysis"),
    ("D2N078", "Philip", "Right knee pain"),
    ("D2N077", "Diana Scott", "Right wrist injury"),
    ("D2N068", "Brian White", "Follow-up of chronic problems"),
    ("D2N200", "Theresa", "Headache and high blood pressure"),
]
LABEL = {"doctor": "Doctor", "patient": "Patient", "patient_guest": "Companion", "unknown": "Speaker"}
OUT = ROOT / "testpersoner"
CASE = ROOT / "demo" / "cases" / "chest-pain"


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    store = get_store()
    rows = []
    for i, (eid, name, reason) in enumerate(PEOPLE, start=1):
        enc = store.get(eid)
        slug = re.sub(r"[^a-z0-9]+", "-", f"{i:02d} {name} {reason}".lower()).strip("-")
        folder = OUT / slug
        folder.mkdir()
        lines = index_transcript(enc.variants["clean"])
        (folder / "transcript.txt").write_text("\n".join(f"{LABEL[l.speaker]}: {l.text}" for l in lines) + "\n", encoding="utf-8")
        files = ["transcript.txt"]
        if eid == "D2N164":  # samma filer som exempelfallet, så att de inspelade svaren gäller
            shutil.copy(CASE / "note-correct.txt", folder / "journal.txt")
            shutil.copy(CASE / "note-wrong.txt", folder / "journal-med-fel.txt")
            files += ["journal.txt", "journal-med-fel.txt"]
        else:
            (folder / "journal.txt").write_text(enc.reference_note.strip() + "\n", encoding="utf-8")
            files.append("journal.txt")
        rows.append((f"{i:02d}", name, reason, slug, len(lines), files))

    readme = [
        "# Testpersoner", "",
        "Tio patienter att ladda upp under **New visit**. Varje mapp har samtalet (`transcript.txt`) och en journal (`journal.txt`).", "",
        "- **Hela flödet:** ladda upp bara `transcript.txt`, kör *Check the visit*, låt AI:n skriva utkastet och granska det.",
        "- **Tillägget:** ladda upp `transcript.txt` och `journal.txt` och gå direkt till *Check note*.", "",
        "| # | Patient | Besvär | Repliker | Filer |", "|---|---|---|---|---|",
    ]
    for num, name, reason, slug, turns, files in rows:
        readme.append(f"| {num} | [{name}]({slug}/) | {reason} | {turns} | {', '.join(f'`{f}`' for f in files)} |")
    readme += [
        "", "**Om journalerna:**",
        "- **01 Elizabeth Moore** har en kort läkaranteckning som stämmer (`journal.txt`) och samma anteckning med sex inplanterade fel "
        "(`journal-med-fel.txt`). För dem finns inspelade AI-svar, så de fungerar även utan API-nyckel och som reserv om API:t krånglar. "
        "Facit finns i [demo/cases/chest-pain/README.md](../demo/cases/chest-pain/README.md).",
        "- **Övriga** har expertjournalen ur datasetet och granskas med det riktiga API:t. Expertjournalerna brukar innehålla några "
        "påståenden utan stöd i samtalet.",
        "- **09 Brian White** spelades in med en röstassistent ('dax'), så samtalet innehåller kommandon till den.",
        "", "Data: ACI-Bench (Yim m.fl., 2023, CC BY 4.0). Rollspelade samtal, inga riktiga patienter.", "",
    ]
    (OUT / "README.md").write_text("\n".join(readme), encoding="utf-8")
    for row in rows:
        print(row[3])


if __name__ == "__main__":
    main()
