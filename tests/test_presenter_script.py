import json

from scripts.presenter.compose import caption_cues
from scripts.presenter.verify_script import SCRIPT_JSON, validate


def test_general_public_presenter_script_contract():
    spec = json.loads(SCRIPT_JSON.read_text())
    assert validate(spec) == []


def test_caption_cues_do_not_leave_a_one_word_orphan():
    words = [
        ("MemoryStand", 0.0, 0.4),
        ("gave", 0.4, 0.7),
        ("attacks", 0.7, 1.1),
        ("none", 1.1, 1.4),
        ("and", 1.4, 1.6),
        ("preserved", 1.6, 2.1),
        ("every", 2.1, 2.4),
        ("honest", 2.4, 2.8),
        ("case.", 2.8, 3.2),
    ]
    cues = caption_cues(words)
    assert cues[-1][2] == "MemoryStand gave attacks none and preserved every honest case."
