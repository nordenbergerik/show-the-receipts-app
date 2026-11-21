"""Bygger exempelgranskningar för frontenden från förinspelade LLM-svar.

demo/llm/*.json innehåller svar på exakt de förfrågningar appen skickar
(demo/export_requests.py), antingen skrivna för hand ("_source": "hand") eller
av en Claude-agent som fick stå i API:ts ställe ("_source": "agent"). Svaren
körs genom samma pipeline och kvittokontroll som riktiga API-svar och märks
så att de inte kan förväxlas med en riktig granskning. Resultatet hamnar i
frontend/samples/<filnamn>.json.

Kör: .venv/Scripts/python -m demo.make_samples
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

from backend.audit import run_audit
from backend.cache import AuditCache
from backend.config import ROOT, get_settings
from backend.data import get_store
from backend.notes import notes_for
from backend.schemas import AuditRequest, ClaimsLLM, CoverageLLM

DEMO = ROOT / "demo"
OUT = ROOT / "frontend" / "samples"
SOURCE_LABEL = {"hand": "example (hand-written)", "agent": "simulated (Claude agent)"}


class ReplayLLM:
    def __init__(self, claims: ClaimsLLM, coverage: CoverageLLM) -> None:
        self.claims, self.coverage = claims, coverage

    async def parse(self, *, system, content, output_format):
        return self.claims if output_format is ClaimsLLM else self.coverage


async def build(path: Path) -> bool:
    raw = json.loads(path.read_text(encoding="utf-8"))
    enc = get_store().get(raw["encounter_id"])
    note = next(n for n in notes_for(enc) if n["id"] == raw["note_id"])
    for claim in raw["claims"]["claims"]:
        claim.setdefault("correction", "")  # äldre inspelningar saknar fältet
    claims = ClaimsLLM.model_validate(raw["claims"])
    coverage = CoverageLLM.model_validate(raw["coverage"])
    with tempfile.TemporaryDirectory() as tmp:
        result = await run_audit(
            AuditRequest(encounter_id=raw["encounter_id"], note_text=note["note_text"]),
            store=get_store(),
            llm=ReplayLLM(claims, coverage),
            cache=AuditCache(Path(tmp)),
            settings=get_settings(),
        )
    source = raw.get("_source", "hand")
    result.meta.model = SOURCE_LABEL.get(source, source)
    result.meta.prompt_version = "replay"
    result.meta.latency_ms = 0

    dropped_quotes = sum(len(r.evidence) - len(c.evidence) for r, c in zip(claims.claims, result.claims) if c.status != "unsupported" or c.receipt_failed)
    failed = [c for c in result.claims if c.receipt_failed]
    no_span = [c for c in result.claims if c.note_span is None]
    dropped_additions = len(coverage.additions) - len(result.suggested_additions)

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{path.stem}.json"
    out.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=1), encoding="utf-8")
    s = result.summary
    print(f"{out.relative_to(ROOT)} [{source}]: {s.total} påståenden "
          f"({s.supported} stöds, {s.inferred} slutsats, {s.contradicted} motsägs, {s.unsupported} saknar grund), "
          f"{len(result.suggested_additions)} tillägg, {len(result.follow_up_questions)} frågor")
    for c in failed:
        print(f"  kvitto underkänt: {c.claim_id} '{c.note_quote}'")
    for c in no_span:
        print(f"  hittades inte i anteckningen: {c.claim_id} '{c.note_quote}'")
    if dropped_quotes:
        print(f"  {dropped_quotes} citat underkändes av kvittokontrollen")
    if dropped_additions:
        print(f"  {dropped_additions} tillägg föll bort i kvittokontrollen")
    return not (failed or no_span or dropped_additions)


async def main() -> int:
    results = [await build(p) for p in sorted((DEMO / "llm").glob("*.json"))]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
