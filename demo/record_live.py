"""Förberäknar ett demofall mot det riktiga Claude-API:t.

Kör varje AI-steg en gång (besökskontroll, SOAP-utkast, granskning av utkastet,
granskning av läkarens egen anteckning, de föreslagna frågorna) genom samma
endpoints som webbläsaren använder och sparar

- svaren som inspelningar i demo/recordings/, så att knapparna i appen svarar
  direkt för exakt samma förfrågan, och
- endpoint-svaren i demo/cases/<fall>/stages.json, som /api/demo-visits gör om
  till inbyggda demobesök i olika steg.

  .venv/Scripts/python -m demo.record_live cough-kyle            # allt på nytt
  .venv/Scripts/python -m demo.record_live cough-kyle --only-new # bara det som saknas

Kostar riktiga pengar (ungefär 0,3–0,5 USD per fall med Sonnet). Skriver aldrig
ut nyckeln.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

os.environ["DEMO_REPLAY"] = "0"  # bara riktiga anrop
os.environ["PREFER_RECORDINGS"] = "0"  # spela in på nytt även om en inspelning finns
os.environ["AUTH_PASSWORD"] = ""  # skriptet går direkt mot API:t, utan inloggning

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import app, get_cache, get_llm  # noqa: E402
from backend.cache import AuditCache  # noqa: E402
from backend.config import get_settings  # noqa: E402
from backend.llm import ClaudeLLM  # noqa: E402
from demo.record import CASES, REPLAY, normalize_transcript, note_text  # noqa: E402


class Recording:
    """Riktiga anrop som också sparas som inspelningar."""

    def __init__(self, live: ClaudeLLM, only_new: bool = False) -> None:
        self.live, self.only_new = live, only_new

    async def parse(self, *, system: str, content: list[dict[str, Any]], output_format: type) -> Any:
        if self.only_new and REPLAY.path_for(output_format.__name__, content).exists():
            print(f"  kept {output_format.__name__} (already recorded)")
            return REPLAY.load(content, output_format)
        result = await self.live.parse(system=system, content=content, output_format=output_format)
        path = REPLAY.save(output_format.__name__, content, result.model_dump(mode="json"), source="live")
        print(f"  recorded {output_format.__name__} -> {path.name}")
        return result


def post(client: TestClient, url: str, body: dict) -> dict:
    res = client.post(url, json=body)
    if res.status_code != 200:
        raise SystemExit(f"{url} failed with {res.status_code}: {res.text[:300]}")
    return res.json()


def main(case: str, only_new: bool = False) -> int:
    settings = get_settings()
    if not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")):
        print("No API key in the environment or .env.")
        return 1
    folder = CASES / case
    config = json.loads((folder / "case.json").read_text(encoding="utf-8"))
    transcript = normalize_transcript((folder / "transcript.txt").read_text(encoding="utf-8"))
    source = {"transcript_text": transcript, "chief_complaint": config.get("reason", "")}
    tmp = tempfile.TemporaryDirectory()
    app.dependency_overrides[get_llm] = lambda: Recording(ClaudeLLM(settings), only_new)
    app.dependency_overrides[get_cache] = lambda: AuditCache(Path(tmp.name))
    client = TestClient(app)

    print(f"Model {settings.model}. Live calls, this takes a few minutes.")
    print("Visit check")
    visit_check = post(client, "/api/visit-check", source)
    print("Draft (SOAP)")
    draft = post(client, "/api/draft-note", {**source, "note_format": "soap"})
    print("Note check of the draft")
    audit_draft = post(client, "/api/audit", {**source, "note_text": draft["note_text"]})
    audits = {}
    for name in config.get("notes", []):
        print(f"Note check of {name}")
        audits[name] = post(client, "/api/audit", {**source, "note_text": note_text(folder, name)})
    asks = []
    for question in config.get("questions", []):
        print(f"Ask: {question}")
        asks.append(post(client, "/api/ask", {**source, "question": question, "note_text": draft["note_text"]}))

    stages = {"visit_check": visit_check, "draft": draft, "audit_draft": audit_draft, "audits": audits, "asks": asks}
    out = folder / "stages.json"
    out.write_text(json.dumps(stages, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {out.relative_to(folder.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sys.exit(main(args[0] if args else "cough-kyle", only_new="--only-new" in sys.argv))
