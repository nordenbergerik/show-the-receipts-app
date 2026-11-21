from collections import Counter

import pytest

from backend.data import VARIANTS, get_store
from backend.transcript import index_transcript


@pytest.fixture(scope="module")
def store():
    return get_store()


def test_all_encounters_loaded(store):
    assert len(store.encounters) == 207


def test_split_sizes(store):
    sizes = Counter(e.split for e in store.encounters.values())
    assert sizes == {
        "train": 67,
        "valid": 20,
        "clinicalnlp_taskB_test1": 40,
        "clinicalnlp_taskC_test2": 40,
        "clef_taskC_test3": 40,
    }


def test_every_encounter_has_clean_dialogue_note_and_metadata(store):
    for enc in store.encounters.values():
        assert enc.variants["clean"].strip(), enc.encounter_id
        assert enc.reference_note.strip(), enc.encounter_id
        assert enc.source_id, enc.encounter_id
        assert enc.dataset in {"aci", "virtscribe", "virtassist"}


def test_asr_variants_are_attached(store):
    counts = Counter(v for e in store.encounters.values() for v in e.variants)
    assert counts["clean"] == 207
    for variant in ("asr", "asrcorr", "humantrans"):
        assert counts[variant] > 0, variant
    assert set(counts) <= set(VARIANTS)
    # virtassist har inga ASR-experiment
    for enc in store.encounters.values():
        if enc.dataset == "virtassist":
            assert set(enc.variants) == {"clean"}


def test_asr_pairs_refer_to_same_visit(store):
    """asr och asrcorr ska vara samma samtal (samma rad-antal ± lite brus)."""
    pairs = [e for e in store.encounters.values() if {"asr", "asrcorr"} <= set(e.variants)]
    assert pairs
    for enc in pairs:
        n_asr = len(index_transcript(enc.variants["asr"]))
        n_corr = len(index_transcript(enc.variants["asrcorr"]))
        assert abs(n_asr - n_corr) <= max(3, n_corr // 5), enc.encounter_id


def test_all_dialogues_index_cleanly(store):
    for enc in store.encounters.values():
        for variant, dialogue in enc.variants.items():
            lines = index_transcript(dialogue)
            assert lines, (enc.encounter_id, variant)
            assert [l.line_id for l in lines] == list(range(1, len(lines) + 1))
            assert all(l.text for l in lines)


def test_lookup_helpers(store):
    enc = store.get("D2N068")
    assert enc is not None
    assert enc.patient_name == "Brian White"
    assert enc.chief_complaint == "follow-up of chronic problems"
    assert store.get("NOPE") is None
