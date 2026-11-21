"""Inbyggda demobesök och inspelade svar före riktiga anrop."""
from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from backend.app import app
from backend.cases import demo_visits
from backend.replay import ReplayLLM
from backend.schemas import VisitCheckLLM


def run(coro):
    return asyncio.run(coro)


class Live:
    def __init__(self) -> None:
        self.calls = 0

    async def parse(self, *, system, content, output_format):
        self.calls += 1
        return output_format(missed=[], decision_flags=[])


def test_recorded_answer_is_used_before_a_live_call(tmp_path):
    from backend import usage
    from backend.llm import RecordedFirst

    content = [{"type": "text", "text": "<transcript>\n[01] [doctor] hi\n</transcript>"}]
    recorded = ReplayLLM(tmp_path)
    live = Live()
    llm = RecordedFirst(live, recorded)
    run(llm.parse(system="s", content=content, output_format=VisitCheckLLM))
    assert live.calls == 1  # ingen inspelning: riktigt anrop

    recorded.save("VisitCheckLLM", content, {"missed": [], "decision_flags": []}, source="test")
    calls = usage.start()
    run(llm.parse(system="s", content=content, output_format=VisitCheckLLM))
    assert live.calls == 1  # inspelningen användes, inget nytt anrop
    assert [c.source for c in calls] == ["recorded"]


def test_demo_visits_cover_every_step_of_kyle():
    visits = [v for v in demo_visits() if v["visit"]["id"].startswith("demo-cough-kyle-")]
    assert [v["visit"]["reason"].split(" · ")[1] for v in visits] == [
        "Step 1: visit recorded", "Step 2: visit checked", "Step 3: note drafted", "Step 4: note checked", "The doctor's own note",
    ]
    step1, step2, step3, step4, own = visits
    assert step1["work"] is None and not step1["visit"]["note"]
    assert step2["work"]["visitCheck"]["missed"] and "transcript" not in step2["work"]["visitCheck"]
    assert step3["visit"]["note"] and step3["work"]["audit"] is None
    # the checked note is the note the visit shows, so the page does not mark the check as stale
    assert step4["work"]["audit"]["note_text"] == step4["visit"]["note"]
    assert step4["review"]["problems"] == sum(c["status"] in ("contradicted", "unsupported") for c in step4["work"]["audit"]["claims"])
    assert own["visit"]["note"] and own["work"] is None


def test_every_ai_step_of_the_demo_visits_is_recorded():
    """Knapparna i demobesöken ska svara med inspelningar, utan API-anrop."""
    from backend.config import get_settings
    from backend.llm import LLMError

    class NoLive:
        async def parse(self, **_):
            raise LLMError("live call in a demo visit", 599)

    from backend.app import get_llm
    from backend.llm import RecordedFirst

    app.dependency_overrides[get_llm] = lambda: RecordedFirst(NoLive(), ReplayLLM(get_settings().recordings_dir))
    try:
        client = TestClient(app)
        visits = [v for v in demo_visits() if v["visit"]["id"].startswith("demo-cough-kyle-")]
        step1, _, step3, _, own = visits
        source = {"transcript_text": step1["visit"]["transcript"], "chief_complaint": "anything"}
        assert client.post("/api/visit-check", json=source).status_code == 200
        assert client.post("/api/draft-note", json={**source, "note_format": "soap"}).json()["note_text"] == step3["visit"]["note"]
        for v in (step3, own):
            assert client.post("/api/audit", json={**source, "note_text": v["visit"]["note"]}).status_code == 200
        for q in client.post("/api/ask/suggestions", json=source).json()["questions"]:
            assert client.post("/api/ask", json={**source, "question": q, "note_text": own["visit"]["note"]}).status_code == 200
    finally:
        app.dependency_overrides.pop(get_llm, None)


def test_demo_visits_endpoint():
    res = TestClient(app).get("/api/demo-visits")
    assert res.status_code == 200
    assert any(v["visit"]["patient"] == "Kyle" for v in res.json())
