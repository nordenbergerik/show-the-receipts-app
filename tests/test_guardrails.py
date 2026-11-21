from backend.guardrails import (
    check_claims,
    check_coverage,
    find_span,
    normalize,
    quote_matches,
    summarize,
)
from backend.schemas import AdditionLLM, ClaimLLM, ClaimsLLM, CoverageLLM, EvidenceLLM, FollowUpLLM
from backend.transcript import index_transcript

TRANSCRIPT = index_transcript(
    "[doctor] so what brings you in today ?\n"
    "[patient] i've had chest pain for two days , it goes down my left arm .\n"
    "[doctor] any shortness of breath ?\n"
    "[patient] no , no shortness of breath .\n"
    "[doctor] i'm going to order an ekg and start you on aspirin 81 mg ."
)


def claim(status="supported", note_quote="chest pain 2d", evidence=None, text="Chest pain for two days."):
    return ClaimLLM(
        claim_id="x",
        note_quote=note_quote,
        text=text,
        status=status,
        evidence=evidence if evidence is not None else [EvidenceLLM(line_id=2, quote="chest pain for two days")],
        explanation="",
        correction="",
    )


# ------------------------------------------------------------- citat


def test_normalize_handles_tokenized_punctuation():
    assert normalize("hi , brian .") == normalize("Hi, Brian.") == "hi brian"


def test_quote_matches_exact_and_tokenized():
    assert quote_matches("chest pain for two days, it goes", TRANSCRIPT[1].text)


def test_quote_matches_tolerates_small_typos():
    assert quote_matches("chest pian for two days", TRANSCRIPT[1].text)


def test_quote_rejects_text_not_on_line():
    assert not quote_matches("pain radiates to the jaw", TRANSCRIPT[1].text)


def test_quote_longer_than_line_is_rejected():
    padded = TRANSCRIPT[2].text + " and also fever and chills for a week"
    assert not quote_matches(padded, TRANSCRIPT[2].text)


# ------------------------------------------------------------- positioner


def test_find_span_exact_and_case_insensitive():
    note = "Chest pain 2d. No SOB."
    assert find_span(note, "Chest pain 2d").model_dump() == {"start": 0, "end": 13}
    assert find_span(note, "no sob").model_dump() == {"start": 15, "end": 21}


def test_find_span_prefers_occurrence_after_cursor():
    note = "pain L arm. pain L arm."
    assert find_span(note, "pain L arm", start_at=5).start == 12


def test_find_span_fuzzy_and_missing():
    note = "Pt reports chest pain x2 days, radiating to L arm."
    span = find_span(note, "radiating to the L arm")
    assert span is not None and note[span.start : span.end].startswith("radiating")
    assert find_span(note, "completely unrelated sentence about knees") is None


# ------------------------------------------------------------- påståenden


def test_valid_receipt_keeps_status():
    [c] = check_claims(ClaimsLLM(claims=[claim()]), TRANSCRIPT, "chest pain 2d")
    assert c.status == "supported" and not c.receipt_failed
    assert c.evidence[0].line_id == 2
    assert c.note_span.model_dump() == {"start": 0, "end": 13}


def test_fabricated_receipt_is_downgraded():
    fake = [EvidenceLLM(line_id=2, quote="pain started after a fall")]
    [c] = check_claims(ClaimsLLM(claims=[claim(evidence=fake)]), TRANSCRIPT, "chest pain 2d")
    assert c.status == "unsupported" and c.receipt_failed and c.evidence == []


def test_unknown_line_id_is_dropped():
    bad = [EvidenceLLM(line_id=99, quote="chest pain"), EvidenceLLM(line_id=2, quote="chest pain")]
    [c] = check_claims(ClaimsLLM(claims=[claim(evidence=bad)]), TRANSCRIPT, "chest pain 2d")
    assert [e.line_id for e in c.evidence] == [2]


def test_contradicted_with_valid_receipt_stays_contradicted():
    ev = [EvidenceLLM(line_id=4, quote="no , no shortness of breath")]
    [c] = check_claims(ClaimsLLM(claims=[claim("contradicted", "SOB+", ev, "Has shortness of breath.")]), TRANSCRIPT, "SOB+")
    assert c.status == "contradicted" and c.evidence


def test_unsupported_claim_has_no_evidence():
    [c] = check_claims(ClaimsLLM(claims=[claim("unsupported")]), TRANSCRIPT, "chest pain 2d")
    assert c.status == "unsupported" and c.evidence == [] and not c.receipt_failed


def test_claim_ids_are_renumbered():
    claims = check_claims(ClaimsLLM(claims=[claim(), claim()]), TRANSCRIPT, "chest pain 2d")
    assert [c.claim_id for c in claims] == ["C1", "C2"]


# ------------------------------------------------------------- tillägg


def test_additions_need_real_receipts_and_skip_duplicates():
    claims = check_claims(ClaimsLLM(claims=[claim()]), TRANSCRIPT, "chest pain 2d")
    coverage = CoverageLLM(
        additions=[
            AdditionLLM(text="Pain radiates to the left arm.", why="Red flag", priority="high",
                        evidence=[EvidenceLLM(line_id=2, quote="it goes down my left arm")]),
            AdditionLLM(text="Patient has a fever.", why="x", priority="low",
                        evidence=[EvidenceLLM(line_id=3, quote="fever")]),
            AdditionLLM(text="Chest pain for two days.", why="dup", priority="low",
                        evidence=[EvidenceLLM(line_id=2, quote="chest pain")]),
        ],
        follow_up_questions=[FollowUpLLM(category="Red flags", question="Any sweating?", why="ACS")],
    )
    additions, follow_ups = check_coverage(coverage, TRANSCRIPT, claims)
    assert [a.text for a in additions] == ["Pain radiates to the left arm."]
    assert additions[0].addition_id == "A1"
    assert follow_ups[0].question == "Any sweating?"


def test_summary_counts():
    claims = check_claims(
        ClaimsLLM(claims=[claim(), claim("inferred"), claim("unsupported"), claim(evidence=[])]),
        TRANSCRIPT,
        "chest pain 2d",
    )
    s = summarize(claims)
    assert (s.total, s.supported, s.inferred, s.unsupported) == (4, 1, 1, 2)
    assert s.with_receipt_pct == 50.0
