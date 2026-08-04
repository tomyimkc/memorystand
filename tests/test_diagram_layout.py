# SPDX-License-Identifier: Apache-2.0
"""The architecture diagram must not ship with text cut off.

Layout bugs in a generated image are invisible in code review. The first render of
docs/architecture.png clipped six panels' bullets mid-sentence and painted three edge labels
on top of boxes, and none of that was apparent from reading render_architecture.py -- only
from opening the PNG. The same class of failure hid a missing image.save() in the video
pipeline, which printed "wrote ..." while saving nothing.

So the renderer measures its own layout and refuses to write a diagram whose content does not
fit. This test runs that machinery, so a future edit that adds one bullet too many fails here
rather than shipping a half-sentence to a judge.

Deliberately NOT asserted: byte-for-byte reproducibility of the PNG. Font rasterisation
differs across machines and OS versions, so pinning the hash would fail for reasons that have
nothing to do with correctness. What is pinned is the property that actually matters -- every
panel's content fits inside its box.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "render_architecture.py"


def _load():
    if str(REPO_ROOT / "scripts" / "video") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "scripts" / "video"))
    spec = importlib.util.spec_from_file_location("render_architecture", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not SCRIPT.is_file(), reason="diagram renderer not present")
def test_every_panel_fits_inside_its_box(tmp_path, monkeypatch):
    module = _load()
    monkeypatch.setattr(module, "OUT", tmp_path / "architecture.png", raising=False)
    module._OVERFLOWS.clear()

    rc = module.main()

    assert not module._OVERFLOWS, (
        "these panels do not fit even at the minimum font size, so their bullets would be "
        f"cut off on screen: {[t for t, *_ in module._OVERFLOWS]}"
    )
    assert rc == 0


@pytest.mark.skipif(not SCRIPT.is_file(), reason="diagram renderer not present")
def test_the_committed_diagram_exists_and_is_the_right_shape():
    """Devpost's diagram field takes a PNG; a broken or missing file fails the submission."""
    from PIL import Image

    png = REPO_ROOT / "docs" / "architecture.png"
    assert png.is_file(), "docs/architecture.png is missing"
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    with Image.open(png) as im:
        assert im.size == (1920, 1080), f"expected 1920x1080, got {im.size}"
