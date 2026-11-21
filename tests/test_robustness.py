"""Felfall: svar i fel format, delvis misslyckade anrop och spårade svar."""
import asyncio
from types import SimpleNamespace

import pydantic
import pytest
from fastapi.testclient import TestClient

from backend.app import app, get_cache, get_llm
from backend.cache import AuditCache
from backend.config import get_settings
from backend.guardrails import MAX_ADDITIONS, check_claims, check_coverage
from backend.llm import ClaudeLLM, LLMError
from backend.schemas import AdditionLLM, ClaimLLM, ClaimsLLM, CoverageLLM, DecisionsLLM, EvidenceLLM
from backend.transcript import index_transcript

ENCOUNTER = "D2N068"
NOTE = "58M CHF, HTN. Fatigue x5 wks."
GOOD_CLAIMS = ClaimsLLM(claims=[ClaimLLM(claim_id="1", note_quote="Fatigue x5 wks", text="Fatigue.", status="supported",
                                         evidence=[EvidenceLLM(line_id=1, quote="hi , brian")], explanation="", correction="")])


# ------------------------------------------------------------- ClaudeLLM


def response(parsed, stop="end_turn"):
    usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_read_input_tokens=0)
    return SimpleNamespace(parsed_output=parsed, stop_reason=stop, usage=usage)


class FakeStream:
    def __init__(self, result):
        self.result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self):
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class FakeMessages:
    """Som SDK:ns client.beta.messages: stream() ger en async context manager."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = 0
        self.kwargs = None

    def stream(self, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return FakeStream(self.results.pop(0))


def claude_with(results):
    llm = ClaudeLLM(get_settings())
    messages = FakeMessages(results)
    llm._client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return llm, messages


def run(coro):
    return asyncio.run(coro)


def validation_error():
    try:
        ClaimsLLM.model_validate({"claims": "not a list"})
    except pydantic.ValidationError as exc:
        return exc


def test_wrong_format_is_retried_once_then_succeeds():
    llm, messages = claude_with([response(None), response(GOOD_CLAIMS)])
    assert run(llm.parse(system="s", content=[], output_format=ClaimsLLM)) == GOOD_CLAIMS
    assert messages.calls == 2


def test_validation_error_is_retried():
    llm, messages = claude_with([validation_error(), response(GOOD_CLAIMS)])
    assert run(llm.parse(system="s", content=[], output_format=ClaimsLLM)) == GOOD_CLAIMS
    assert messages.calls == 2


def test_wrong_format_twice_gives_clear_error():
    llm, messages = claude_with([response(None), response({"claims": "nope"})])
    with pytest.raises(LLMError) as err:
        run(llm.parse(system="s", content=[], output_format=ClaimsLLM))
    assert err.value.status_code == 502 and "format" in str(err.value)
    assert messages.calls == 2


def test_dict_output_is_validated_against_schema():
    llm, _ = claude_with([response(GOOD_CLAIMS.model_dump())])
    assert isinstance(run(llm.parse(system="s", content=[], output_format=ClaimsLLM)), ClaimsLLM)


def test_refusal_and_cut_off_are_not_retried():
    llm, messages = claude_with([response(None, stop="refusal")])
    with pytest.raises(LLMError) as err:
        run(llm.parse(system="s", content=[], output_format=ClaimsLLM))
    assert err.value.status_code == 422 and messages.calls == 1
    llm, messages = claude_with([response(None, stop="max_tokens")])
    with pytest.raises(LLMError):
        run(llm.parse(system="s", content=[], output_format=ClaimsLLM))
    assert messages.calls == 1


# ------------------------------------------------------------- granskningen som helhet


class PartlyFailingLLM:
    def __init__(self, fail_claims=False, fail_coverage=False):
        self.fail_claims, self.fail_coverage = fail_claims, fail_coverage

    async def parse(self, *, system, content, output_format):
        if output_format is ClaimsLLM:
            if self.fail_claims:
                raise LLMError("The model answered in an unexpected format twice. Try again in a moment.", 502)
            return GOOD_CLAIMS
        if output_format is DecisionsLLM:
            return DecisionsLLM(flags=[])
        if self.fail_coverage:
            raise RuntimeError("boom")
        return CoverageLLM(additions=[], follow_up_questions=[])


@pytest.fixture
def make_client(tmp_path):
    def make(llm):
        app.dependency_overrides[get_llm] = lambda: llm
        app.dependency_overrides[get_cache] = lambda: AuditCache(tmp_path)
        return TestClient(app)
    yield make
    app.dependency_overrides.clear()


def test_failed_suggestions_still_return_the_review(make_client, tmp_path):
    with make_client(PartlyFailingLLM(fail_coverage=True)) as c:
        r = c.post("/api/audit", json={"encounter_id": ENCOUNTER, "note_text": NOTE})
    assert r.status_code == 200
    data = r.json()
    assert data["claims"] and data["warnings"]
    assert not list((tmp_path / "audits").glob("*.json"))  # ofullständigt resultat cachas inte


def test_failed_claims_give_clear_error(make_client):
    with make_client(PartlyFailingLLM(fail_claims=True)) as c:
        r = c.post("/api/audit", json={"encounter_id": ENCOUNTER, "note_text": NOTE})
    assert r.status_code == 502 and "format" in r.json()["detail"]


# ------------------------------------------------------------- rimlighetsgränser


def test_runaway_output_is_capped_and_blanks_dropped():
    lines = index_transcript("[doctor] hi , brian .\n[patient] good to see you")
    blank = ClaimLLM(claim_id="x", note_quote="  ", text="", status="supported", evidence=[], explanation="", correction="")
    claims = check_claims(ClaimsLLM(claims=[blank, *GOOD_CLAIMS.claims]), lines, NOTE)
    assert len(claims) == 1
    many = CoverageLLM(
        additions=[AdditionLLM(text=f"Item {i}", why="w", priority="low", evidence=[EvidenceLLM(line_id=2, quote="good to see you")]) for i in range(20)],
        follow_up_questions=[],
    )
    additions, _ = check_coverage(many, lines, [])
    assert len(additions) == MAX_ADDITIONS


# ------------------------------------------------------------- kostnad och reserv


def test_usage_and_cost_are_recorded_per_call():
    from backend import usage

    calls = usage.start()
    llm, messages = claude_with([SimpleNamespace(
        parsed_output=GOOD_CLAIMS, stop_reason="end_turn", model="claude-fable-5-2",
        usage=SimpleNamespace(input_tokens=1000, output_tokens=2000, cache_read_input_tokens=3000, cache_creation_input_tokens=0),
    )])
    run(llm.parse(system="s", content=[], output_format=ClaimsLLM))
    [call] = calls
    assert (call.task, call.input_tokens, call.output_tokens, call.cache_read_tokens) == ("ClaimsLLM", 1000, 2000, 3000)
    # Fable 5.2: $8 in, $40 out, $0.20 cache read per million tokens
    assert call.cost_usd == round((1000 * 8 + 2000 * 40 + 3000 * 0.2) / 1e6, 4)
    assert messages.kwargs["model"] and messages.kwargs["output_config"]["effort"]
    assert usage.summarize(calls).cost_usd == call.cost_usd


def test_live_failure_falls_back_to_a_recording_only_when_one_exists(tmp_path):
    from backend import usage
    from backend.llm import LiveWithFallback
    from backend.replay import ReplayLLM
    from backend.visit import visit_check_content
    from backend.schemas import VisitCheckLLM

    lines = index_transcript("[doctor] hi , brian .\n[patient] good to see you")
    content = visit_check_content(lines, "")
    recorded = ReplayLLM(tmp_path)
    down = LLMError("Could not reach the Claude API. Check the network connection.", 503)
    llm, _ = claude_with([down, down])
    both = LiveWithFallback(llm, recorded)
    with pytest.raises(LLMError):  # ingen inspelning: felet syns
        run(both.parse(system="s", content=content, output_format=VisitCheckLLM))
    recorded.save("VisitCheckLLM", content, {"missed": [], "decision_flags": []}, source="test")
    calls = usage.start()
    assert run(both.parse(system="s", content=content, output_format=VisitCheckLLM)) == VisitCheckLLM(missed=[], decision_flags=[])
    assert [c.source for c in calls] == ["recorded-fallback"]


def test_bad_key_is_not_replaced_by_a_recording(tmp_path):
    from backend.llm import LiveWithFallback
    from backend.replay import ReplayLLM

    llm, _ = claude_with([LLMError("The API key is invalid. Check ANTHROPIC_API_KEY.", 500)])
    with pytest.raises(LLMError) as err:
        run(LiveWithFallback(llm, ReplayLLM(tmp_path)).parse(system="s", content=[], output_format=ClaimsLLM))
    assert err.value.status_code == 500
