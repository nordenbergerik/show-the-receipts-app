from backend.transcript import index_transcript, render_for_prompt


def test_basic_speakers_and_ids():
    lines = index_transcript("[doctor] hi , brian .\n[patient] hi , good to see you .\n")
    assert [(l.line_id, l.speaker, l.text) for l in lines] == [
        (1, "doctor", "hi , brian ."),
        (2, "patient", "hi , good to see you ."),
    ]


def test_blank_lines_are_skipped_and_ids_stay_sequential():
    lines = index_transcript("[doctor] a\n\n   \n[patient] b\n")
    assert [l.line_id for l in lines] == [1, 2]


def test_patient_guest_and_unknown_tags():
    lines = index_transcript("[patient_guest] i'm his wife\n[nurse] vitals are fine\nno tag here")
    assert [l.speaker for l in lines] == ["patient_guest", "unknown", "unknown"]
    assert lines[1].text == "[nurse] vitals are fine"  # okänd etikett behålls i texten
    assert lines[2].text == "no tag here"


def test_empty_turn_is_dropped():
    assert index_transcript("[doctor]   \n[patient] ok")[0].text == "ok"


def test_render_for_prompt_zero_pads():
    lines = index_transcript("\n".join(f"[doctor] line {i}" for i in range(1, 12)))
    rendered = render_for_prompt(lines).splitlines()
    assert rendered[0] == "[01] [doctor] line 1"
    assert rendered[10] == "[11] [doctor] line 11"


def test_render_for_prompt_widens_past_99():
    lines = index_transcript("\n".join(f"[patient] l{i}" for i in range(120)))
    assert render_for_prompt(lines).splitlines()[0].startswith("[001] ")
