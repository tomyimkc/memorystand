import json

from scripts.presenter.compose import caption_cues, rebalance_caption_lines
from scripts.presenter import lipsync
from scripts.presenter.verify_script import SCRIPT_JSON, validate


def test_general_public_presenter_script_contract():
    spec = json.loads(SCRIPT_JSON.read_text())
    assert validate(spec) == []
    assert sum(len(beat.get("broll", {})) for beat in spec["beats"]) >= 5
    assert all(len(beat["shots"]) == 1 for beat in spec["beats"])
    assert all(
        len(beat["panelData"].get("bullets", [])) <= 2
        for beat in spec["beats"]
    )


def test_presenter_script_rejects_overlapping_wording():
    spec = json.loads(SCRIPT_JSON.read_text())
    spec["beats"][4]["shots"][0] = spec["beats"][1]["shots"][0]
    assert any("overlapping wording" in error for error in validate(spec))


def test_presenter_script_allows_reusing_one_accurate_noun():
    spec = json.loads(SCRIPT_JSON.read_text())
    spec["beats"][4]["shots"][0] = (
        "CloudWatch blocks the wrong direction or amount until a person reviews it."
    )
    assert not any("overlapping wording" in error for error in validate(spec))


def test_every_broll_beat_has_plain_english_callouts():
    spec = json.loads(SCRIPT_JSON.read_text())
    visuals = [
        visual
        for beat in spec["beats"]
        for visual in beat.get("broll", {}).values()
    ]
    assert visuals
    assert all(1 <= len(visual["callouts"]) <= 3 for visual in visuals)
    assert all(
        1 <= len(callout) <= 48
        for visual in visuals
        for callout in visual["callouts"]
    )


def test_why_beat_shows_correlation_is_not_causation_in_plain_language():
    spec = json.loads(SCRIPT_JSON.read_text())
    beat = next(b for b in spec["beats"] if b["id"] == "09-why-false-cause")
    visual = beat["broll"]["0"]
    displayed = " ".join([visual["headline"], *visual["callouts"]]).lower()
    assert "alert quiet" in displayed
    assert "latency" in displayed
    assert "flat" in displayed
    assert "before:" in displayed and "after:" in displayed
    assert "claimed improvement:" in displayed


def test_why_spoken_line_names_the_outside_metric_and_causal_error():
    spec = json.loads(SCRIPT_JSON.read_text())
    beat = next(b for b in spec["beats"] if b["id"] == "09-why-false-cause")
    spoken = beat["shots"][0].lower()
    assert "outside service latency graph" in spoken
    assert "alert stopped" in spoken
    assert "does not prove" in spoken
    assert "reboot fixed" in spoken


def test_presenter_script_rejects_broll_outside_generated_artifacts():
    spec = json.loads(SCRIPT_JSON.read_text())
    beat = next(b for b in spec["beats"] if b.get("broll"))
    beat["broll"]["0"]["source"] = "/tmp/unreviewed.mp4"
    assert any("broll source" in error for error in validate(spec))


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


def test_burned_caption_wrap_does_not_leave_a_visual_orphan():
    assert rebalance_caption_lines(
        ["MemoryStand gives autonomous keys only to memories", "that"]
    ) == [
        "MemoryStand gives autonomous keys only",
        "to memories that",
    ]


def test_lipsync_cache_is_invalidated_when_a_clip_is_regenerated(tmp_path, monkeypatch):
    clip = tmp_path / "shot.mp4"
    cache = tmp_path / "lip-offsets.json"
    calls = []

    def fake_measure(path):
        calls.append(path.read_bytes())
        return 41.7, 0.8

    monkeypatch.setattr(lipsync, "measure", fake_measure)

    clip.write_bytes(b"first Grok render")
    assert lipsync.offsets([clip], cache)["shot"] == 0.0417
    assert lipsync.offsets([clip], cache)["shot"] == 0.0417
    clip.write_bytes(b"regenerated Grok render")
    assert lipsync.offsets([clip], cache)["shot"] == 0.0417

    assert calls == [b"first Grok render", b"regenerated Grok render"]
    receipt = json.loads(cache.read_text())["shot"]
    assert receipt["sourceSha256"] == lipsync._sha256(clip)
