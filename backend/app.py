"""FastAPI-app: API under /api och frontenden som statiska filer på /."""
from __future__ import annotations

import hmac
import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from backend.audit import AuditError, run_audit
from backend.cache import AuditCache
from backend.config import Settings, get_settings
from backend.data import VARIANTS, DataStore, get_store
from backend.llm import ClaudeLLM, StructuredLLM
from backend.schemas import AuditRequest, AuditResult, Variant
from backend.transcript import index_transcript

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Show the Receipts", docs_url="/api/docs", openapi_url="/api/openapi.json")

_llm: StructuredLLM | None = None


def get_llm() -> StructuredLLM:
    global _llm
    if _llm is None:
        _llm = ClaudeLLM(get_settings())
    return _llm


def get_cache(settings: Annotated[Settings, Depends(get_settings)]) -> AuditCache:
    return AuditCache(settings.cache_dir)


@app.get("/api/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {
        "status": "ok",
        "model": settings.model,
        "offline": settings.demo_offline,
        "access_code_required": settings.access_code is not None,
    }


@app.get("/api/encounters")
def list_encounters(store: Annotated[DataStore, Depends(get_store)]) -> list[dict]:
    return [
        {
            "encounter_id": e.encounter_id,
            "split": e.split,
            "dataset": e.dataset,
            "chief_complaint": e.chief_complaint,
            "patient": e.patient_name,
            "variants": list(e.variants),
        }
        for e in store.list()
    ]


@app.get("/api/encounters/{encounter_id}")
def get_encounter(
    encounter_id: str,
    store: Annotated[DataStore, Depends(get_store)],
    variant: Annotated[Variant, Query()] = "clean",
) -> dict:
    enc = store.get(encounter_id)
    if enc is None:
        raise HTTPException(404, f"Okänt encounter_id: {encounter_id}")
    if variant not in enc.variants:
        raise HTTPException(404, f"{encounter_id} saknar varianten '{variant}'")
    return {
        "encounter_id": enc.encounter_id,
        "split": enc.split,
        "dataset": enc.dataset,
        "chief_complaint": enc.chief_complaint,
        "patient": enc.patient_name,
        "variant": variant,
        "variants": {v: VARIANTS[v] for v in enc.variants},
        "transcript": [line.model_dump() for line in index_transcript(enc.variants[variant])],
        "reference_note": enc.reference_note,
    }


@app.post("/api/audit")
async def audit(
    req: AuditRequest,
    store: Annotated[DataStore, Depends(get_store)],
    llm: Annotated[StructuredLLM, Depends(get_llm)],
    cache: Annotated[AuditCache, Depends(get_cache)],
    settings: Annotated[Settings, Depends(get_settings)],
    x_access_code: Annotated[str | None, Header()] = None,
) -> AuditResult:
    allow_live = settings.access_code is None or (
        x_access_code is not None and hmac.compare_digest(x_access_code, settings.access_code)
    )
    try:
        return await run_audit(req, store=store, llm=llm, cache=cache, settings=settings, allow_live=allow_live)
    except AuditError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


# Frontenden monteras sist så att /api/* har företräde.
app.mount("/", StaticFiles(directory=get_settings().frontend_dir, html=True), name="frontend")
