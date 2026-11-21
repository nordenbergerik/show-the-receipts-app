"""Besökskontroll, journalutkast, frågor till journalen och inspelade svar."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from backend.app import app, get_llm
from backend.llm import LLMError
from backend.replay import ReplayLLM, replay_key
from backend.schemas import (
    AskLLM,
    DecisionFlagLLM,
    DraftNoteLLM,
    EvidenceLLM,
    FindingLLM,
    MissedItemLLM,
    VisitCheckLLM,
)
from backend.transcript import index_transcript
from backend.visit import ask_content, audit_content, draft_content, record_lines, visit_check_content

ENCOUNTER = "D2N068"  # Brian White
TRANSCRIPT = "[doctor] hi , brian .\n[patient] i feel tired and dizzy .\n[doctor] any chest pain ?\n[patient] no ."


class FakeLLM:
    def __init__(self):
        self.seen = []

    async def parse(self, *, system, content, output_format):
        self.seen.append((output_format, content))
        if output_format is VisitCheckLLM:
            return VisitCheckLLM(
                missed=[MissedItemLLM(category="Red flags", question="Any fainting?", why="Dizziness",
                                      evidence=[EvidenceLLM(line_id=2, quote="tired and dizzy"), EvidenceLLM(line_id=2, quote="fake")])],
                decision_flags=[DecisionFlagLLM(category="Tests", severity="consider", title="Check orthostatics?",
                                                concern="Dizziness", consider="Measure orthostatic BP", evidence=[])],
            )
        if output_format is DraftNoteLLM:
            return DraftNoteLLM(note_text="History: Fatigue and dizziness. Denies chest pain.\r\n")
        if output_format is AskLLM:
            return AskLLM(
                verdict="evidence_found",
                summary="Possibly.",
                findings=[
                    FindingLLM(stance="supports", text="Dizzy", evidence=[EvidenceLLM(line_id=2, quote="dizzy")]),
                    FindingLLM(stance="supports", text="Invented", evidence=[EvidenceLLM(line_id=9, quote="syncope")]),
                ],
                where_to_look=["Orthostatic BP", " "],
            )
        raise AssertionError(output_format)


@pytest.fixture
def client():
    fake = FakeLLM()
    app.dependency_overrides[get_llm] = lambda: fake
    with TestClient(app) as c:
        c.fake = fake
        yield c
    app.dependency_overrides.clear()


def test_visit_check_validates_receipts(client):
    r = client.post("/api/visit-check", json={"transcript_text": TRANSCRIPT})
    assert r.status_code == 200, r.text
    data = r.json()
    [missed] = data["missed"]
    assert [e["quote"] for e in missed["evidence"]] == ["tired and dizzy"]
    assert data["decision_flags"][0]["flag_id"] == "V1"
    assert len(data["transcript"]) == 4


def test_draft_note_cleans_text_and_sends_accepted_points(client):
    r = client.post("/api/draft-note", json={"encounter_id": ENCOUNTER, "accepted": ["Ask about fainting", " "]})
    assert r.status_code == 200, r.text
    assert r.json()["note_text"] == "History: Fatigue and dizziness. Denies chest pain."
    _, content = client.fake.seen[-1]
    assert content[-1]["text"].startswith("<accepted_points>") and "Ask about fainting" in content[-1]["text"]


def test_ask_drops_unquoted_findings_and_keeps_disclaimer(client):
    r = client.post("/api/ask", json={"transcript_text": TRANSCRIPT, "note_text": "Dizzy x2d.", "question": "Any evidence of syncope?"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert [f["text"] for f in data["findings"]] == ["Dizzy"]
    assert data["verdict"] == "evidence_found"
    assert data["where_to_look"] == ["Orthostatic BP"]
    assert "can be wrong" in data["disclaimer"]
    assert data["record"][-1] == {"line_id": 5, "speaker": "note", "text": "Dizzy x2d."}


def test_ask_without_real_receipts_cannot_claim_evidence(client):
    class NoReceipts(FakeLLM):
        async def parse(self, **kwargs):
            return AskLLM(verdict="evidence_found", summary="Yes.", where_to_look=[],
                          findings=[FindingLLM(stance="supports", text="x", evidence=[EvidenceLLM(line_id=1, quote="not there")])])
    app.dependency_overrides[get_llm] = lambda: NoReceipts()
    r = client.post("/api/ask", json={"transcript_text": TRANSCRIPT, "question": "Any evidence of syncope?"})
    data = r.json()
    assert data["verdict"] == "no_evidence_found" and data["findings"] == [] and data["warnings"]


def test_request_validation(client):
    assert client.post("/api/ask", json={"transcript_text": TRANSCRIPT, "question": "x"}).status_code == 422
    assert client.post("/api/draft-note", json={"transcript_text": TRANSCRIPT, "accepted": ["a"] * 13}).status_code == 422
    assert client.post("/api/visit-check", json={"encounter_id": "NOPE"}).status_code == 404


# ------------------------------------------------------------- inspelade svar


LINES = index_transcript(TRANSCRIPT)


def test_replay_key_ignores_what_the_user_controls():
    base = replay_key("VisitCheckLLM", visit_check_content(LINES, ""))
    assert replay_key("VisitCheckLLM", visit_check_content(LINES, "Dizziness")) == base
    assert replay_key("DraftNoteLLM", draft_content(LINES, "", ["a"])) == replay_key("DraftNoteLLM", draft_content(LINES, "x", []))
    ask_a, _ = ask_content(LINES, "", "note one", "Any syncope?")
    ask_b, _ = ask_content(LINES, "", "a different note", "Any syncope?")
    assert replay_key("AskLLM", ask_a) == replay_key("AskLLM", ask_b)
    # men anteckningen avgör vid granskning, och formatet skiljer anropen åt
    assert replay_key("ClaimsLLM", audit_content(LINES, "", "a")) != replay_key("ClaimsLLM", audit_content(LINES, "", "b"))
    assert replay_key("ClaimsLLM", audit_content(LINES, "", "a")) != replay_key("CoverageLLM", audit_content(LINES, "", "a"))


def test_replay_roundtrip_and_missing_recording(tmp_path):
    replay = ReplayLLM(tmp_path)
    content = visit_check_content(LINES, "")
    replay.save("VisitCheckLLM", content, {"missed": [], "decision_flags": []}, source="test")
    result = asyncio.run(replay.parse(system="s", content=content, output_format=VisitCheckLLM))
    assert result == VisitCheckLLM(missed=[], decision_flags=[])
    with pytest.raises(LLMError) as err:
        asyncio.run(replay.parse(system="s", content=draft_content(LINES, "", []), output_format=DraftNoteLLM))
    assert err.value.status_code == 503 and "recorded" in str(err.value)


def test_damaged_recording_gives_clear_error(tmp_path):
    replay = ReplayLLM(tmp_path)
    content = visit_check_content(LINES, "")
    replay.path_for("VisitCheckLLM", content).write_text("{not json", encoding="utf-8")
    with pytest.raises(LLMError) as err:
        asyncio.run(replay.parse(system="s", content=content, output_format=VisitCheckLLM))
    assert err.value.status_code == 502


def test_record_lines_number_the_note_after_the_transcript():
    record = record_lines(LINES, "Line A\n\nLine B")
    assert [(l.line_id, l.speaker) for l in record[-2:]] == [(5, "note"), (6, "note")]


def test_suggested_questions_for_example_case(client):
    from pathlib import Path
    transcript = (Path(__file__).resolve().parent.parent / "demo" / "cases" / "chest-pain" / "transcript.txt").read_text(encoding="utf-8")
    transcript = "\n".join(
        line.replace("Doctor: ", "[doctor] ", 1).replace("Patient: ", "[patient] ", 1) for line in transcript.splitlines()
    )
    r = client.post("/api/ask/suggestions", json={"transcript_text": transcript})
    assert r.status_code == 200 and len(r.json()["questions"]) == 3
    assert client.post("/api/ask/suggestions", json={"transcript_text": TRANSCRIPT}).json() == {"questions": []}
