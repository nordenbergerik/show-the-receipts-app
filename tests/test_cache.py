"""Cachning: samma granskning återanvänds bara när exakt samma sak skulle skickas
till samma modell, trasiga cachefiler stoppar inget, och svar med patientdata
sparas aldrig av webbläsaren."""
import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.app import app, get_cache, get_llm
from backend.cache import AuditCache, audit_key
from tests.test_api import FakeLLM

TRANSCRIPT = "[doctor] hi , brian . how are you ?\n[patient] tired , for about five weeks ."
NOTE = "Fatigue x5 wks."


@pytest.fixture
def client(tmp_path):
    fake = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: fake
    app.dependency_overrides[get_cache] = lambda: AuditCache(tmp_path)
    with TestClient(app) as c:
        c.fake = fake
        yield c
    app.dependency_overrides.clear()


def audit(client, **changes):
    body = {"transcript_text": TRANSCRIPT, "note_text": NOTE, "chief_complaint": "Fatigue", **changes}
    r = client.post("/api/audit", json=body)
    assert r.status_code == 200
    return r.json()


def test_same_audit_is_reused(client):
    assert audit(client)["meta"]["cached"] is False
    calls = client.fake.calls
    again = audit(client)
    assert again["meta"]["cached"] is True and client.fake.calls == calls


@pytest.mark.parametrize("changes", [
    {"note_text": NOTE + " Denies cough."},           # another note
    {"note_text": NOTE + "\n"},                       # even whitespace: the exact text is what is checked
    {"chief_complaint": "Cough"},                     # the chief complaint is part of what the model reads
    {"transcript_text": TRANSCRIPT + "\n[doctor] okay ."},
])
def test_anything_the_model_reads_gives_a_new_audit(client, changes):
    audit(client)
    assert audit(client, **changes)["meta"]["cached"] is False


def test_other_model_or_effort_gives_a_new_audit(client, monkeypatch):
    audit(client)
    for env in ({"CLAUDE_EFFORT_CLAIMS": "medium"}, {"CLAUDE_MODEL": "claude-fable-5-2"}):
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        config.get_settings.cache_clear()
        try:
            assert audit(client)["meta"]["cached"] is False
        finally:
            for key in env:
                monkeypatch.delenv(key)
            config.get_settings.cache_clear()


def test_key_depends_on_every_part():
    base = {"model": "m", "efforts": {"ClaimsLLM": "high"}, "prompt_version": "v1", "content": [{"type": "text", "text": "x"}]}
    keys = {audit_key(**base)}
    for part, value in (("model", "m2"), ("efforts", {"ClaimsLLM": "low"}), ("prompt_version", "v2"),
                        ("content", [{"type": "text", "text": "y"}])):
        keys.add(audit_key(**{**base, part: value}))
    assert len(keys) == 5
    assert audit_key(**base) == audit_key(**dict(base))


def test_broken_cache_file_is_a_miss_not_an_error(client, tmp_path):
    audit(client)
    [path] = list((tmp_path / "audits").glob("*.json"))
    path.write_text("{ not json", encoding="utf-8")
    result = audit(client)
    assert result["meta"]["cached"] is False and result["claims"]
    assert audit(client)["meta"]["cached"] is True  # rewritten after the new audit


def test_api_answers_are_never_stored_by_the_browser(client):
    assert client.get("/api/health").headers["cache-control"] == "no-store"
    assert client.post("/api/audit", json={"transcript_text": TRANSCRIPT, "note_text": NOTE}).headers["cache-control"] == "no-store"
    # the page itself is revalidated, so a new version is picked up at once
    assert client.get("/login").headers["cache-control"] == "no-cache"


def test_sign_in_redirects_also_carry_cache_headers(monkeypatch):
    monkeypatch.setenv("AUTH_PASSWORD", "test-only-password")
    with TestClient(app) as c:
        page = c.get("/app/", follow_redirects=False)
        api = c.get("/api/me")
    assert page.status_code == 303 and page.headers["cache-control"] == "no-cache"
    assert api.status_code == 401 and api.headers["cache-control"] == "no-store"
