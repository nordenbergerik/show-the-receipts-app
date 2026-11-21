"""Bygger exempelgranskningar för frontenden från handskrivna LLM-svar.

Svaren i demo/llm/*.json körs genom exakt samma pipeline och kvittokontroll
som riktiga Claude-svar, men märks som exempel så att de aldrig kan förväxlas
med en riktig granskning. Resultatet hamnar i frontend/samples/.

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
from backend.schemas import AuditRequest, ClaimsLLM, CoverageLLM

DEMO = ROOT / "demo"
OUT = ROOT / "frontend" / "samples"


class ReplayLLM:
    def __init__(self, claims: ClaimsLLM, coverage: CoverageLLM) -> None:
        self.claims, self.coverage = claims, coverage

    async def parse(self, *, system, content, output_format):
        return self.claims if output_format is ClaimsLLM else self.coverage


async def build(path: Path, notes: dict) -> bool:
    raw = json.loads(path.read_text(encoding="utf-8"))
    note = next(n for n in notes[raw["encounter_id"]] if n["id"] == raw["note_id"])
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
    result.meta.model = "exempel"
    result.meta.prompt_version = "handskrivet"
    result.meta.latency_ms = 0

    ok = True
    for raw_claim, claim in zip(claims.claims, result.claims):
        if len(claim.evidence) != len(raw_claim.evidence) or claim.receipt_failed or claim.note_span is None:
            ok = False
            print(f"  ! {claim.claim_id} '{claim.note_quote}': {len(raw_claim.evidence)}->{len(claim.evidence)} kvitton, span={claim.note_span}")
    dropped = len(coverage.additions) - len(result.suggested_additions)
    if dropped:
        ok = False
        print(f"  ! {dropped} tillägg föll bort i kvittokontrollen")

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"{path.stem}.json"
    out.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{'OK ' if ok else 'VARNING'} {out.relative_to(ROOT)}: {result.summary.model_dump()}")
    return ok


async def main() -> int:
    notes = json.loads((DEMO / "notes.json").read_text(encoding="utf-8"))
    results = [await build(p, notes) for p in sorted((DEMO / "llm").glob("*.json"))]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
