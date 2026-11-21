"""API-tester med en falsk LLM: inga nätverksanrop och ingen API-nyckel behövs."""
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from backend.app import app, get_cache, get_llm
from backend.cache import AuditCache
from backend.config import get_settings
from backend.schemas import AdditionLLM, ClaimLLM, ClaimsLLM, CoverageLLM, DecisionFlagLLM, DecisionsLLM, EvidenceLLM, FollowUpLLM

ENCOUNTER = "D2N068"  # virtassist, Brian White
NOTE = "58M CHF, HTN. Fatigue x5 wks. Denies cough."


class FakeLLM:
    def __init__(self):
        self.calls = 0

    async def parse(self, *, system, content, output_format):
        self.calls += 1
        if output_format is ClaimsLLM:
            return ClaimsLLM(claims=[
                ClaimLLM(claim_id="1", note_quote="Fatigue x5 wks", text="Fatigue for five weeks.", status="supported",
                         evidence=[EvidenceLLM(line_id=1, quote="hi , brian")], explanation="ok", correction=""),
                ClaimLLM(claim_id="2", note_quote="Denies cough", text="Denies cough.", status="contradicted",
                         evidence=[EvidenceLLM(line_id=2, quote="something never said")], explanation="x", correction="Coughs sometimes."),
            ])
        if output_format is DecisionsLLM:
            return DecisionsLLM(flags=[DecisionFlagLLM(category="Diagnosis", severity="important", title="Is it really CHF?",
                                                       concern="c", consider="k", evidence=[EvidenceLLM(line_id=2, quote="good to see you"), EvidenceLLM(line_id=1, quote="made up")])])
        return CoverageLLM(
            additions=[AdditionLLM(text="Good to see you.", why="x", priority="low",
                                   evidence=[EvidenceLLM(line_id=2, quote="good to see you")])],
            follow_up_questions=[FollowUpLLM(category="Red flags", title="Rest pain?", question="Chest pain at rest?", why="x")],
        )


@pytest.fixture
def client(tmp_path):
    fake = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: fake
    app.dependency_overrides[get_cache] = lambda: AuditCache(tmp_path)
    with TestClient(app) as c:
        c.fake = fake
        yield c
    app.dependency_overrides.clear()


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_list_and_get_encounter(client):
    items = client.get("/api/encounters").json()
    assert len(items) == 207
    enc = client.get(f"/api/encounters/{ENCOUNTER}").json()
    assert enc["patient"] == "Brian White"
    assert enc["transcript"][0]["line_id"] == 1
    assert client.get("/api/encounters/NOPE").status_code == 404
    assert client.get(f"/api/encounters/{ENCOUNTER}?variant=asr").status_code == 404  # virtassist saknar ASR


def test_audit_runs_guardrails_and_caches(client):
    body = {"encounter_id": ENCOUNTER, "note_text": NOTE}
    first = client.post("/api/audit", json=body)
    assert first.status_code == 200, first.text
    data = first.json()
    statuses = {c["note_quote"]: c["status"] for c in data["claims"]}
    assert statuses == {"Fatigue x5 wks": "supported", "Denies cough": "unsupported"}  # påhittat kvitto nedgraderat
    assert data["claims"][1]["receipt_failed"] is True
    assert data["claims"][0]["note_span"] == {"start": NOTE.index("Fatigue"), "end": NOTE.index("Fatigue") + 14}
    assert [a["text"] for a in data["suggested_additions"]] == ["Good to see you."]
    [flag] = data["decision_flags"]
    assert flag["title"] == "Is it really CHF?" and [e["line_id"] for e in flag["evidence"]] == [2]  # påhittat kvitto bort
    assert data["meta"]["cached"] is False
    assert client.fake.calls == 3

    second = client.post("/api/audit", json=body).json()
    assert second["meta"]["cached"] is True
    assert client.fake.calls == 3  # inga nya LLM-anrop


def test_audit_validation(client):
    assert client.post("/api/audit", json={"note_text": "x"}).status_code == 422
    assert client.post("/api/audit", json={"encounter_id": "A", "transcript_text": "b", "note_text": "x"}).status_code == 422
    assert client.post("/api/audit", json={"encounter_id": ENCOUNTER, "note_text": "x" * 10_001}).status_code == 422
    assert client.post("/api/audit", json={"encounter_id": "NOPE", "note_text": "x"}).status_code == 404


def test_audit_with_free_text_transcript(client):
    r = client.post("/api/audit", json={"transcript_text": "[doctor] hi , brian .\n[patient] good to see you", "note_text": NOTE})
    assert r.status_code == 200
    assert r.json()["variant"] is None


def test_offline_mode_and_access_code(client):
    base = get_settings()
    body = {"encounter_id": ENCOUNTER, "note_text": NOTE}

    app.dependency_overrides[get_settings] = lambda: replace(base, demo_offline=True)
    assert client.post("/api/audit", json=body).status_code == 409

    app.dependency_overrides[get_settings] = lambda: replace(base, access_code="hemligt")
    assert client.post("/api/audit", json=body).status_code == 401
    ok = client.post("/api/audit", json=body, headers={"X-Access-Code": "hemligt"})
    assert ok.status_code == 200
    # Förberäknade svar kräver ingen kod
    assert client.post("/api/audit", json=body).status_code == 200


def test_frontend_is_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "Show the Receipts" in r.text


def test_missing_api_key_gives_clear_error(tmp_path, monkeypatch):
    """Utan nyckel ska användaren få ett begripligt fel, inte en 500:a."""
    from backend.llm import ClaudeLLM

    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_PROFILE"):
        monkeypatch.delenv(var, raising=False)
    app.dependency_overrides[get_llm] = lambda: ClaudeLLM(get_settings())
    app.dependency_overrides[get_cache] = lambda: AuditCache(tmp_path)
    try:
        with TestClient(app) as c:
            r = c.post("/api/audit", json={"encounter_id": ENCOUNTER, "note_text": NOTE})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 503, r.text
    assert "API key" in r.json()["detail"]


def test_frontend_files_are_revalidated(client):
    assert client.get("/app.js").headers["cache-control"] == "no-cache"
    assert "cache-control" not in client.get("/api/health").headers
