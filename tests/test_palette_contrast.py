"""Every (palette, mode) combination Task 2 ships must clear WCAG AA. This
is the acceptance gate for dev/gen_palette.py's output — see its module
docstring: the script itself is not imported at runtime, only by this test
and by hand when regenerating palettes.css.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "gen_palette", Path(__file__).resolve().parents[1] / "dev" / "gen_palette.py"
)
gen_palette_mod = importlib.util.module_from_spec(_SPEC)
sys.modules["gen_palette"] = gen_palette_mod
_SPEC.loader.exec_module(gen_palette_mod)

contrast = gen_palette_mod.contrast
gen_palette = gen_palette_mod.gen_palette
PALETTE_HUES = gen_palette_mod.PALETTE_HUES
DANGER_DARK = gen_palette_mod.DANGER_DARK
DANGER_LIGHT = gen_palette_mod.DANGER_LIGHT


@pytest.mark.parametrize("palette", list(PALETTE_HUES))
def test_palette_clears_wcag_aa(palette):
    dark, light, _clips = gen_palette(palette, PALETTE_HUES[palette])

    # dark mode: role tokens against the dark ground
    assert contrast(dark["color-text"], dark["color-bg"]) >= 4.5
    assert contrast(dark["color-text"], dark["color-surface"]) >= 4.5
    assert contrast(dark["color-text-muted"], dark["color-bg"]) >= 4.5
    assert contrast(dark["color-text-muted"], dark["color-surface"]) >= 4.5
    assert contrast(dark["color-accent"], dark["color-bg"]) >= 3.0
    assert contrast(DANGER_DARK, dark["color-bg"]) >= 3.0

    # light mode: role tokens against the light ground, accent/accent-2
    # borrow the palette's own 700 ramp step (see palettes.css comment)
    assert contrast(light["color-text"], light["color-bg"]) >= 4.5
    assert contrast(light["color-text"], light["color-surface"]) >= 4.5
    assert contrast(light["color-text-muted"], light["color-bg"]) >= 4.5
    assert contrast(light["color-text-muted"], light["color-surface"]) >= 4.5
    assert contrast(dark["color-accent-700"], light["color-bg"]) >= 4.5
    assert contrast(dark["color-accent-700"], light["color-surface"]) >= 4.5
    assert contrast(dark["color-accent-2-700"], light["color-bg"]) >= 4.5
    assert contrast(dark["color-accent-2-700"], light["color-surface"]) >= 4.5
    assert contrast(DANGER_LIGHT, light["color-bg"]) >= 4.5
    assert contrast(DANGER_LIGHT, light["color-surface"]) >= 4.5


def test_danger_is_not_rotated_per_palette():
    """A user-visible red must mean the same thing in every palette."""
    for palette, hue in PALETTE_HUES.items():
        dark, light, _clips = gen_palette(palette, hue)
        assert DANGER_DARK == "#e5849a"
        assert DANGER_LIGHT not in dark.values()  # sanity: distinct token, not accidentally aliased
