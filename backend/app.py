"""FastAPI-app: API under /api och frontenden som statiska filer på /."""
from __future__ import annotations

import hmac
import logging
from typing import Annotated

from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.assist import run_ask, run_draft_note, run_visit_check
from backend.audit import AuditError, run_audit
from backend.auth import (
    COOKIE,
    SESSION_SECONDS,
    User,
    auth_enabled,
    check_credentials,
    get_current_user,
    is_https,
    make_token,
    read_token,
    record_failure,
    reset_failures,
    too_many_attempts,
)
from backend.cache import AuditCache
from backend.cases import suggested_questions
from backend.config import Settings, get_settings
from backend.data import VARIANTS, DataStore, get_store
from backend import usage
from backend.llm import ClaudeLLM, LiveWithFallback, StructuredLLM
from backend.replay import ReplayLLM
from backend.schemas import (
    AskRequest,
    AskResult,
    AuditRequest,
    AuditResult,
    DraftNoteRequest,
    DraftNoteResult,
    Variant,
    VisitCheckRequest,
    VisitCheckResult,
)
from backend.transcript import index_transcript
from backend.visit import VisitError, resolve_visit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = FastAPI(title="Show the Receipts", docs_url="/api/docs", openapi_url="/api/openapi.json")


# Utan inloggning nås bara inloggningssidan, dess stilmall och hälsokontrollen
# (som Render använder).
PUBLIC_PATHS = {"/login", "/api/login", "/api/health", "/style.css"}


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if auth_enabled() and path not in PUBLIC_PATHS and read_token(request.cookies.get(COOKIE)) is None:
        if path.startswith("/api/"):
            return JSONResponse({"detail": "Sign in to continue."}, status_code=401)
        return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
    return await call_next(request)


@app.middleware("http")
async def no_stale_frontend(request: Request, call_next):
    """Läggs till sist och ligger därför ytterst, så att även inloggningens svar
    får rubrikerna. Webbläsaren frågar alltid efter en ny version av sidans filer
    (ETag gör att oförändrade filer ändå bara ger ett snabbt 304-svar)."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        # patientdata: sparas aldrig av webbläsaren eller någon proxy
        response.headers["Cache-Control"] = "no-store"
    else:
        response.headers["Cache-Control"] = "no-cache"
    return response


class LoginRequest(BaseModel):
    username: str = Field(max_length=100)
    password: str = Field(max_length=200)


@app.get("/login", include_in_schema=False)
def login_page() -> FileResponse:
    return FileResponse(get_settings().frontend_dir / "login.html")


@app.post("/api/login")
def login(body: LoginRequest, request: Request) -> JSONResponse:
    client = request.client.host if request.client else "unknown"
    if too_many_attempts(client):
        raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
    if not auth_enabled() or not check_credentials(body.username, body.password):
        record_failure(client)
        raise HTTPException(401, "Wrong username or password.")
    reset_failures(client)
    response = JSONResponse({"ok": True})
    response.set_cookie(COOKIE, make_token(body.username.strip()), max_age=SESSION_SECONDS, httponly=True,
                        samesite="lax", secure=is_https(request), path="/")
    return response


@app.post("/api/logout")
def logout() -> JSONResponse:
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE, path="/")
    return response


_llm: StructuredLLM | None = None


def get_llm() -> StructuredLLM:
    """Riktiga Claude-anrop, eller inspelade svar när demon körs utan API-nyckel."""
    global _llm
    if _llm is None:
        settings = get_settings()
        recorded = ReplayLLM(settings.recordings_dir)
        _llm = recorded if settings.replay else LiveWithFallback(ClaudeLLM(settings), recorded)
    return _llm


def allow_live(
    settings: Annotated[Settings, Depends(get_settings)],
    x_access_code: Annotated[str | None, Header()] = None,
) -> bool:
    return settings.access_code is None or (
        x_access_code is not None and hmac.compare_digest(x_access_code, settings.access_code)
    )


def get_cache(settings: Annotated[Settings, Depends(get_settings)]) -> AuditCache:
    return AuditCache(settings.cache_dir)


@app.get("/api/health")
def health(settings: Annotated[Settings, Depends(get_settings)]) -> dict:
    return {
        "status": "ok",
        "model": settings.model,
        "offline": settings.demo_offline,
        "replay": settings.replay,
        "access_code_required": settings.access_code is not None,
    }


@app.get("/api/me")
def me(user: Annotated[User, Depends(get_current_user)]) -> User:
    return user


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
        raise HTTPException(404, f"Unknown visit: {encounter_id}")
    if variant not in enc.variants:
        raise HTTPException(404, f"{encounter_id} has no '{variant}' transcript")
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


Store = Annotated[DataStore, Depends(get_store)]
LLM = Annotated[StructuredLLM, Depends(get_llm)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
Live = Annotated[bool, Depends(allow_live)]


@app.post("/api/audit")
async def audit(req: AuditRequest, store: Store, llm: LLM, settings: SettingsDep, live: Live,
                cache: Annotated[AuditCache, Depends(get_cache)]) -> AuditResult:
    try:
        calls = usage.start()
        result = await run_audit(req, store=store, llm=llm, cache=cache, settings=settings, allow_live=live)
        if not result.meta.cached:
            result.meta.usage = usage.summarize(calls)
        return result
    except AuditError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.post("/api/visit-check")
async def visit_check(req: VisitCheckRequest, store: Store, llm: LLM, settings: SettingsDep, live: Live) -> VisitCheckResult:
    try:
        calls = usage.start()
        result = await run_visit_check(req, store=store, llm=llm, settings=settings, allow_live=live)
        if not result.meta.cached:
            result.meta.usage = usage.summarize(calls)
        return result
    except AuditError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.post("/api/draft-note")
async def draft_note(req: DraftNoteRequest, store: Store, llm: LLM, settings: SettingsDep, live: Live) -> DraftNoteResult:
    try:
        calls = usage.start()
        result = await run_draft_note(req, store=store, llm=llm, settings=settings, allow_live=live)
        if not result.meta.cached:
            result.meta.usage = usage.summarize(calls)
        return result
    except AuditError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


@app.post("/api/ask/suggestions")
def ask_suggestions(req: VisitCheckRequest, store: Store) -> dict:
    """Föreslagna frågor till journalen (för exempelfallen finns inspelade svar)."""
    try:
        lines, _ = resolve_visit(req, store)
    except VisitError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc
    return {"questions": suggested_questions(lines)}


@app.post("/api/ask")
async def ask(req: AskRequest, store: Store, llm: LLM, settings: SettingsDep, live: Live) -> AskResult:
    try:
        calls = usage.start()
        result = await run_ask(req, store=store, llm=llm, settings=settings, allow_live=live)
        if not result.meta.cached:
            result.meta.usage = usage.summarize(calls)
        return result
    except AuditError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc


# Frontenden monteras sist så att /api/* har företräde.
app.mount("/", StaticFiles(directory=get_settings().frontend_dir, html=True), name="frontend")
