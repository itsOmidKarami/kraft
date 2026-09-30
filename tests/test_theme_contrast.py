"""The new UI's colour tokens clear their contrast floors in every surface ×
accent × amount × mode (UX V2 W1 brief §B), and the checked-in `theme.css` is
what `dev/gen_theme.py` writes. `ng/theme/theme.contrast.test.ts` checks the
same floors on the file itself."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "gen_theme", Path(__file__).resolve().parents[1] / "dev" / "gen_theme.py"
)
gen = importlib.util.module_from_spec(_SPEC)
sys.modules["gen_theme"] = gen
_SPEC.loader.exec_module(gen)

BLOCKS, ACCENTS, _LOG = gen.generate()
contrast = gen.contrast
COMBOS = [(s, m, a) for s in gen.SURFACES for m in gen.MODES for a in gen.AMOUNTS]


def _accent(t: dict, x: str, m: str, a: str) -> str:
    v = ACCENTS[x, m, a]
    return t["text"] if v == "var(--text)" else v


@pytest.mark.parametrize(("surface", "mode", "amount"), COMBOS)
def test_every_combination_clears_its_floors(surface, mode, amount):
    t = BLOCKS[surface, mode, amount]
    for role in ("text", "text-sub", "text-muted"):
        for ground in ("bg", "side", "surface"):
            assert contrast(t[role], t[ground]) >= 4.5, (role, ground)
    for ground in ("bg", "side", "surface"):
        assert contrast(t["text-faint"], t[ground]) >= 3.0, ground
    for role in ("ok", "warn", "bad", "info"):
        assert contrast(t[role], t["bg"]) >= 3.0, (role, "fill")
        assert contrast(t[role], t["surface"]) >= 4.5, (role, "text")
    assert contrast(t["focus"], t["bg"]) >= 3.0
    for x in gen.ACCENTS:
        if amount == "mono" and x != "none":
            continue  # Mono locks the accent to none: 13 choices per surface and mode.
        for ground in ("bg", "surface"):
            assert contrast(_accent(t, x, mode, amount), t[ground]) >= 4.5, (x, ground)


@pytest.mark.parametrize("mode", gen.MODES)
def test_mono_neutrals_have_no_chroma(mode):
    """At mono the five surfaces differ in lightness only."""
    neutral = ("side", "bg", "surface", "surface-2", "line", "border")
    text = ("text", "text-sub", "text-muted", "text-faint")
    grounds = set()
    for s in gen.SURFACES:
        t = BLOCKS[s, mode, "mono"]
        for role in neutral + text:
            r, g, b = (t[role][i : i + 2] for i in (1, 3, 5))
            assert r == g == b, (s, role, t[role])
        grounds.add(t["bg"])
    assert len(grounds) == len(gen.SURFACES)


def test_mono_and_none_accent_is_the_text_colour():
    for (x, _m, a), v in ACCENTS.items():
        if x == "none" or a == "mono":
            assert v == "var(--text)"
        else:
            assert re.fullmatch(r"#[0-9a-f]{6}", v)


def test_the_checked_in_theme_css_is_the_generators_output():
    assert gen.OUT.read_text() == gen.render(BLOCKS, ACCENTS), "run dev/gen_theme.py"
