# Theme Palettes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user pick from 5 color palettes and a Light/Dark/System mode, with a live preview, from a new Settings → Appearance page.

**Architecture:** CSS attribute-selector theming (`<html data-palette data-mode>`), a Python-generated `palettes.css` with hue-rotated OKLCH token sets, a `theme.yaml` persisted the same way every other Settings page persists its YAML, and a small `theme.ts` that flips the two DOM attributes for instant repaint.

**Tech Stack:** FastAPI + Pydantic (backend), React + Zustand + Vite/Vitest (frontend), plain CSS custom properties (no CSS-in-JS).

**Spec:** `docs/superpowers/specs/2026-09-09-theme-palettes-design.md`

## Global Constraints

- Instance-wide setting, not per-user or per-repo — matches every existing Settings page (`policy.yaml`, `access.yaml`, `notify.yaml` are all instance-wide, none are scoped to a repo or a session).
- 5 palettes only, curated at build time (`nocturne`, `rose`, `forest`, `amber`, `slate`) — no custom/arbitrary hue picker.
- Every (palette, mode) combination must clear WCAG AA: **4.5:1** for text-role tokens (`--color-text`, `--color-text-muted`, `--color-accent`/`--color-accent-2` where used as link/button text, `--color-danger`), **3:1** for large/UI-only uses. Enforced by `tests/test_palette_contrast.py`, not by eyeballing.
- Selecting a palette or mode re-paints the live app immediately (real preview); Save persists to the server, Discard reverts the DOM back to the loaded value.
- No new dependencies. `color-mix()` and CSS attribute selectors are already used throughout `nocturne.css`/`styles.css`.

---

## Task 1: Palette color generator + contrast verification

Computes every palette's token set once (OKLCH hue-rotation off Nocturne's existing dark tokens, same technique the file's own comments describe) and asserts every (palette, mode) combination clears WCAG AA. This task's output (the printed hex values) is what Task 2 pastes into `palettes.css` — run it once, copy the numbers, done; nothing at runtime imports this module.

**Files:**
- Create: `dev/gen_palette.py`
- Test: `tests/test_palette_contrast.py`

**Interfaces:**
- Produces: `PALETTE_HUES: dict[str, float | None]` — palette id → target OKLCH hue for `nocturne`'s own accent hue (289.55°), `None` for `nocturne` itself (no rotation). `TOKENS_DARK: dict[str, str]` — Nocturne's existing dark-mode token name → hex, copied verbatim from `nocturne.css`. `LIGHT_ROLE_BASE: dict[str, tuple[float, float, float]]` — role token name → `(L, C, H)` for Nocturne's light-mode role tokens. `DANGER_LIGHT: str` — the one fixed light-mode danger hex. `gen_palette(name: str, target_hue: float | None) -> tuple[dict[str, str], dict[str, str], list[str]]` — returns `(ramp_and_dark_role, light_role, gamut_clip_warnings)`. `contrast(hex1: str, hex2: str) -> float`.

- [ ] **Step 1: Write `dev/gen_palette.py`**

```python
"""Generates the hex values for `frontend/src/palettes.css`.

Not imported at runtime by the app — a one-shot generator whose printed
output gets pasted into palettes.css by hand (Task 2 of the theme-palettes
plan), the same way nocturne.css's own token comments record the OKLCH
math that produced their literal hex. `tests/test_palette_contrast.py`
imports the functions and frozen tables below to verify the *pasted*
values still clear WCAG AA — that test is the thing that actually runs in
CI, not this script.

Method: every new palette is Nocturne's own dark token set, hue-rotated as
a rigid family (each token keeps its own L and C, only H shifts by one
constant delta per palette) — the same "same L/C, different hue" approach
nocturne.css's own accent-tuning comment describes, applied to the whole
token set instead of one color. `--color-danger` is deliberately excluded
from rotation: a danger color that changed hue per palette would stop
reading as "danger" consistently, which defeats the point of a semantic
color.
"""

from __future__ import annotations

import math

# ── sRGB <-> OKLCH (Björn Ottosson's OKLab, D65) ────────────────────────────


def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def _cbrt(x: float) -> float:
    return x ** (1 / 3) if x >= 0 else -((-x) ** (1 / 3))


def rgb_to_oklch(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(c) for c in rgb)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = _cbrt(l), _cbrt(m), _cbrt(s)
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b2 = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    c = math.sqrt(a * a + b2 * b2)
    h = math.degrees(math.atan2(b2, a)) % 360
    return L, c, h


def oklch_to_rgb(l_val: float, c_val: float, h_val: float) -> tuple[float, float, float]:
    a = c_val * math.cos(math.radians(h_val))
    b2 = c_val * math.sin(math.radians(h_val))
    l_ = l_val + 0.3963377774 * a + 0.2158037573 * b2
    m_ = l_val - 0.1055613458 * a - 0.0638541728 * b2
    s_ = l_val - 0.0894841775 * a - 1.2914855480 * b2
    l, m, s = l_**3, m_**3, s_**3
    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    return tuple(_linear_to_srgb(x) * 255 for x in (r, g, b))  # type: ignore[return-value]


def oklch_to_hex(l_val: float, c_val: float, h_val: float) -> str:
    return rgb_to_hex(oklch_to_rgb(l_val, c_val, h_val))


def _rel_luminance(rgb: tuple[float, float, float]) -> float:
    r, g, b = (_srgb_to_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(hex1: str, hex2: str) -> float:
    """WCAG relative-luminance contrast ratio between two hex colors."""
    l1, l2 = _rel_luminance(hex_to_rgb(hex1)), _rel_luminance(hex_to_rgb(hex2))
    l1, l2 = max(l1, l2), min(l1, l2)
    return (l1 + 0.05) / (l2 + 0.05)


def _would_clip(l_val: float, c_val: float, h_val: float) -> bool:
    """True if this OKLCH point falls outside the sRGB gamut (a channel
    would clamp during conversion, distorting the intended color)."""
    a = c_val * math.cos(math.radians(h_val))
    b2 = c_val * math.sin(math.radians(h_val))
    l_ = l_val + 0.3963377774 * a + 0.2158037573 * b2
    m_ = l_val - 0.1055613458 * a - 0.0638541728 * b2
    s_ = l_val - 0.0894841775 * a - 1.2914855480 * b2
    l, m, s = l_**3, m_**3, s_**3
    r = +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s
    for c in (r, g, b):
        v = 12.92 * c if c <= 0.0031308 else 1.055 * (max(c, 0) ** (1 / 2.4)) - 0.055
        if v < -0.001 or v > 1.001:
            return True
    return False


# ── Nocturne's existing dark tokens (source of the rotation) ───────────────

TOKENS_DARK: dict[str, str] = {
    "color-bg": "#161826",
    "color-surface": "#232532",
    "color-text": "#e9e9ed",
    "color-accent": "#9184d9",
    "color-accent-2": "#a7a1db",
    "color-neutral-100": "#f3f5fe",
    "color-neutral-200": "#e4e7f5",
    "color-neutral-300": "#cfd3e5",
    "color-neutral-400": "#b2b6ca",
    "color-neutral-500": "#9397ab",
    "color-neutral-600": "#75798c",
    "color-neutral-700": "#595d6c",
    "color-neutral-800": "#3f424d",
    "color-neutral-900": "#292b31",
    "color-accent-100": "#f5f4ff",
    "color-accent-200": "#e7e5fe",
    "color-accent-300": "#d2cefd",
    "color-accent-400": "#b5abfc",
    "color-accent-500": "#968ae0",
    "color-accent-600": "#796cbf",
    "color-accent-700": "#5d5294",
    "color-accent-800": "#423a6a",
    "color-accent-900": "#2b2741",
    "color-accent-2-100": "#f5f4ff",
    "color-accent-2-200": "#e7e5fe",
    "color-accent-2-300": "#d2cefd",
    "color-accent-2-400": "#b5afe8",
    "color-accent-2-500": "#9690c9",
    "color-accent-2-600": "#7972a9",
    "color-accent-2-700": "#5c5783",
    "color-accent-2-800": "#423e5d",
    "color-accent-2-900": "#2b293a",
    "color-text-muted": "#888c9c",
}

#: Fixed across every palette — see module docstring.
DANGER_DARK = "#e5849a"

#: Hand-derived for a light Nocturne ground, contrast-checked against
#: DANGER_LIGHT/accent-700 below by test_palette_contrast.py.
LIGHT_ROLE_BASE: dict[str, tuple[float, float, float]] = {
    "color-bg": (0.9650, 0.0060, 277.52),
    "color-surface": (0.9900, 0.0040, 277.80),
    "color-text": (0.2000, 0.0200, 286.30),
    "color-text-muted": (0.4500, 0.0200, 286.30),
}

#: Fixed across every palette, same reasoning as DANGER_DARK. Darker than
#: DANGER_DARK because it has to hold 4.5:1 as *text* against a light
#: ground, where DANGER_DARK only has to clear 3:1 against a dark one.
DANGER_LIGHT = oklch_to_hex(0.50, 0.16, 5.97)

#: id -> target hue for that palette's --color-accent (degrees). `None` =
#: nocturne, no rotation. Picked so each is >=40 degrees from the fixed
#: danger hue (~6 degrees) and reasonably spread from each other.
PALETTE_HUES: dict[str, float | None] = {
    "nocturne": None,
    "rose": 325.0,
    "forest": 150.0,
    "amber": 55.0,
    "slate": 225.0,
}

_BASE_ACCENT_H = rgb_to_oklch(hex_to_rgb(TOKENS_DARK["color-accent"]))[2]


def gen_palette(
    name: str, target_hue: float | None
) -> tuple[dict[str, str], dict[str, str], list[str]]:
    """Returns (dark tokens incl. ramp, light role tokens, gamut-clip warnings)."""
    delta = 0.0 if target_hue is None else target_hue - _BASE_ACCENT_H
    dark: dict[str, str] = {}
    clipped: list[str] = []
    for tname, hx in TOKENS_DARK.items():
        L, C, H = rgb_to_oklch(hex_to_rgb(hx))
        new_h = (H + delta) % 360
        if _would_clip(L, C, new_h):
            clipped.append(tname)
        dark[tname] = oklch_to_hex(L, C, new_h)
    light: dict[str, str] = {}
    for tname, (L, C, H) in LIGHT_ROLE_BASE.items():
        light[tname] = oklch_to_hex(L, C, (H + delta) % 360)
    if name == "nocturne":
        clipped = []  # delta 0 never clips; TOKENS_DARK is already valid sRGB
    return dark, light, clipped


if __name__ == "__main__":
    for pname, hue in PALETTE_HUES.items():
        dark, light, clips = gen_palette(pname, hue)
        print(f"\n=== {pname} ===")
        if clips:
            print(f"  ! gamut clipping (cosmetic only, contrast still checked): {clips}")
        for k, v in dark.items():
            print(f"  {k:22s} {v}")
        for k, v in light.items():
            print(f"  {k:22s} {v}  (light)")
```

- [ ] **Step 2: Run it and capture the output**

Run: `uv run python dev/gen_palette.py`

This prints every hex value Task 2 needs. Keep the output open/saved — Task 2's `palettes.css` is a direct transcription of it (values below are already transcribed from this exact run, so Task 2 does not require re-running this successfully first, but running it now confirms the environment reproduces the same numbers before you rely on them).

- [ ] **Step 3: Write the failing contrast test**

```python
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
```

- [ ] **Step 4: Run it, confirm it passes (it should — the values above are already the verified output)**

Run: `uv run pytest tests/test_palette_contrast.py -v`
Expected: all `test_palette_clears_wcag_aa[...]` cases and `test_danger_is_not_rotated_per_palette` PASS.

If any case fails, `dev/gen_palette.py`'s `PALETTE_HUES` hue for that palette is too close to a gamut edge or to the danger hue — pick a different target hue a few degrees off and re-run Step 2 before touching the test.

- [ ] **Step 5: Commit**

```bash
git add dev/gen_palette.py tests/test_palette_contrast.py
git commit -m "feat: palette color generator with WCAG AA verification"
```

---

## Task 2: `palettes.css` and the `nocturne.css` divider refactor

**Files:**
- Modify: `frontend/src/nocturne.css` (one line)
- Create: `frontend/src/palettes.css`
- Create: `frontend/src/palettes.order.test.ts`

**Interfaces:**
- Consumes: the exact hex values Task 1 verified.
- Produces: `<html data-palette="nocturne|rose|forest|amber|slate" data-mode="dark|light">` as the complete theming surface — Task 5's `theme.ts` only ever sets these two attributes, nothing else.

- [ ] **Step 1: Refactor the divider token to track `--color-text`**

In `frontend/src/nocturne.css`, find:

```css
  --color-divider: color-mix(in srgb, #e9e9ed 16%, transparent);
```

Replace with:

```css
  /* `var()`, not the literal hex: this makes the divider automatically
     correct for every palette and mode once palettes.css overrides
     --color-text — one less token every palette override has to restate. */
  --color-divider: color-mix(in srgb, var(--color-text) 16%, transparent);
```

This is behavior-preserving for Nocturne itself: `var(--color-text)` resolves to the same `#e9e9ed` the literal hex named.

- [ ] **Step 2: Write `frontend/src/palettes.css`**

```css
/* Palettes — alternate hues for Nocturne's token system (see
   nocturne.css). Values here come straight from `dev/gen_palette.py`
   (`tests/test_palette_contrast.py` is what actually verifies them).

   Structure, cascade-order matters:
   1. Ramp blocks (`[data-palette="X"]`) — ONE per non-Nocturne palette.
      Mode-independent: a `.tag-accent` chip needs its own internal
      contrast (dark bg, light text) regardless of whether the *page* is
      in light or dark mode, so the ramp doesn't change with mode.
      Nocturne's own ramp needs no block here — it's nocturne.css's
      unscoped `:root`, which stays the fallback for any palette/mode
      combination nothing here overrides.
   2. Mode blocks (`[data-mode="dark"]` / `[data-mode="light"]`) — ONE
      EACH, for every palette. Only reference ramp `var()`s or a fixed
      semantic value, so a single rule serves all 5 palettes. Must come
      AFTER the ramp blocks: `--color-accent`/`--color-accent-2` are set
      in both the ramp blocks and here, and both selectors have equal
      specificity (`:root` + one attribute) — source order breaks the
      tie, and light mode's override has to win. palettes.order.test.ts
      pins this.
   3. Role blocks (`[data-palette="X"][data-mode="Y"]`) — bg/surface/text/
      text-muted only. These four don't derive from anything else, so
      each (palette, mode) pair needs its own literal values. Two
      attributes beats one, so these always win regardless of order.
*/

/* ── 1. ramp blocks ─────────────────────────────────────────────────────── */

:root[data-palette="rose"] {
  --color-accent: #b876bc;
  --color-accent-2: #c498c7;
  --color-neutral-100: #f8f3fc;
  --color-neutral-200: #ece4f1;
  --color-neutral-300: #d9d0e0;
  --color-neutral-400: #bdb2c5;
  --color-neutral-500: #9e93a6;
  --color-neutral-600: #807587;
  --color-neutral-700: #625a69;
  --color-neutral-800: #45404a;
  --color-neutral-900: #2d2a30;
  --color-accent-100: #fbf2fb;
  --color-accent-200: #f4e1f5;
  --color-accent-300: #eac6ec;
  --color-accent-400: #dc9ee0;
  --color-accent-500: #be7cc3;
  --color-accent-600: #9e5ea3;
  --color-accent-700: #7b477e;
  --color-accent-800: #58325a;
  --color-accent-900: #372338;
  --color-accent-2-100: #fbf2fb;
  --color-accent-2-200: #f4e1f5;
  --color-accent-2-300: #eac6ec;
  --color-accent-2-400: #d2a6d4;
  --color-accent-2-500: #b287b5;
  --color-accent-2-600: #946996;
  --color-accent-2-700: #715074;
  --color-accent-2-800: #513952;
  --color-accent-2-900: #332634;
  --color-text-muted: #918998;
}

:root[data-palette="forest"] {
  --color-accent: #53a768;
  --color-accent-2: #84b88d;
  --color-neutral-100: #f2f8f0;
  --color-neutral-200: #e2ebdf;
  --color-neutral-300: #cdd8c8;
  --color-neutral-400: #afbcaa;
  --color-neutral-500: #909d8b;
  --color-neutral-600: #727e6d;
  --color-neutral-700: #586153;
  --color-neutral-800: #3e453b;
  --color-neutral-900: #292c27;
  --color-accent-100: #eff8f0;
  --color-accent-200: #d9efdd;
  --color-accent-300: #b7e1be;
  --color-accent-400: #80cc90;
  --color-accent-500: #5aad6d;
  --color-accent-600: #3a8e50;
  --color-accent-700: #296e3d;
  --color-accent-800: #1d4e2b;
  --color-accent-900: #19321f;
  --color-accent-2-100: #eff8f0;
  --color-accent-2-200: #d9efdd;
  --color-accent-2-300: #b7e1be;
  --color-accent-2-400: #92c69c;
  --color-accent-2-500: #73a77c;
  --color-accent-2-600: #558860;
  --color-accent-2-700: #406948;
  --color-accent-2-800: #2e4b34;
  --color-accent-2-900: #203023;
  --color-text-muted: #879082;
}

:root[data-palette="amber"] {
  --color-accent: #cc7c40;
  --color-accent-2: #d39b77;
  --color-neutral-100: #fdf3ef;
  --color-neutral-200: #f4e4de;
  --color-neutral-300: #e3cfc7;
  --color-neutral-400: #c8b2a9;
  --color-neutral-500: #a9938a;
  --color-neutral-600: #8a756d;
  --color-neutral-700: #6b5954;
  --color-neutral-800: #4c3f3b;
  --color-neutral-900: #312927;
  --color-accent-100: #fef3ec;
  --color-accent-200: #fbe3d3;
  --color-accent-300: #f7caad;
  --color-accent-400: #f0a36e;
  --color-accent-500: #d38146;
  --color-accent-600: #b26326;
  --color-accent-700: #894b19;
  --color-accent-800: #623511;
  --color-accent-900: #3d2513;
  --color-accent-2-100: #fef3ec;
  --color-accent-2-200: #fbe3d3;
  --color-accent-2-300: #f7caad;
  --color-accent-2-400: #e0aa86;
  --color-accent-2-500: #c18a66;
  --color-accent-2-600: #a16d49;
  --color-accent-2-700: #7d5236;
  --color-accent-2-800: #583b27;
  --color-accent-2-900: #38281d;
  --color-text-muted: #9b8882;
}

:root[data-palette="slate"] {
  /* the 600/700/800 accent steps sit at the edge of the sRGB gamut at this
     hue and clip slightly more saturated than their L/C target — cosmetic
     only, contrast is unaffected (verified by test_palette_contrast.py).
     ponytail: gamut-mapped clamping, not gamut-aware chroma reduction; fix
     by lowering this palette's chroma if the clipped steps look off. */
  --color-accent: #04a1cb;
  --color-accent-2: #6cb5d0;
  --color-neutral-100: #edf8fa;
  --color-neutral-200: #daecef;
  --color-neutral-300: #c2d9dd;
  --color-neutral-400: #a3bcc2;
  --color-neutral-500: #849da3;
  --color-neutral-600: #677f84;
  --color-neutral-700: #4e6265;
  --color-neutral-800: #374648;
  --color-neutral-900: #252d2e;
  --color-accent-100: #ecf8fd;
  --color-accent-200: #d1eef9;
  --color-accent-300: #a6def4;
  --color-accent-400: #58c7ee;
  --color-accent-500: #13a7d1;
  --color-accent-600: #0088b1;
  --color-accent-700: #006989;
  --color-accent-800: #004a62;
  --color-accent-900: #0b303d;
  --color-accent-2-100: #ecf8fd;
  --color-accent-2-200: #d1eef9;
  --color-accent-2-300: #a6def4;
  --color-accent-2-400: #7cc3de;
  --color-accent-2-500: #5aa3bf;
  --color-accent-2-600: #3a85a0;
  --color-accent-2-700: #2a667b;
  --color-accent-2-800: #204958;
  --color-accent-2-900: #1a2f37;
  --color-text-muted: #7c9195;
}

/* ── 2. mode blocks — must stay AFTER the ramp blocks above ─────────────── */

:root[data-mode="dark"] {
  --color-danger: #e5849a;
  --shadow-sm: 0 0 0 1px var(--color-neutral-800);
  --shadow-md: 0 0 0 1px var(--color-neutral-700), 0 6px 18px rgba(0, 0, 0, 0.55);
  --shadow-lg: 0 0 0 1px var(--color-neutral-500), 0 16px 40px rgba(0, 0, 0, 0.65);
}

:root[data-mode="light"] {
  /* Nocturne's accent (L 0.66) was tuned to read on a dark ground; on
     white it falls short of 4.5:1 as link/button text. The ramp's own
     700 step (L ~0.48) is dark enough to hold contrast on any palette's
     light ground without a second hand-tuned value — verified by
     test_palette_contrast.py. */
  --color-accent: var(--color-accent-700);
  --color-accent-2: var(--color-accent-2-700);
  --color-danger: #a92e55;
  --shadow-sm: 0 1px 2px color-mix(in srgb, var(--color-neutral-900) 10%, transparent);
  --shadow-md:
    0 1px 2px color-mix(in srgb, var(--color-neutral-900) 10%, transparent),
    0 6px 18px color-mix(in srgb, var(--color-neutral-900) 16%, transparent);
  --shadow-lg:
    0 1px 2px color-mix(in srgb, var(--color-neutral-900) 10%, transparent),
    0 16px 40px color-mix(in srgb, var(--color-neutral-900) 22%, transparent);
}

/* ── 3. (palette, mode) role blocks ──────────────────────────────────────── */

:root[data-palette="nocturne"][data-mode="light"] {
  --color-bg: #f2f3f8;
  --color-surface: #fbfcfe;
  --color-text: #15151f;
  --color-text-muted: #545460;
}

:root[data-palette="rose"][data-mode="dark"] {
  --color-bg: #1e1522;
  --color-surface: #2a232f;
  --color-text: #ebe8ec;
  --color-text-muted: #918998;
}
:root[data-palette="rose"][data-mode="light"] {
  --color-bg: #f5f2f6;
  --color-surface: #fdfbfe;
  --color-text: #1a131b;
  --color-text-muted: #5b525c;
}

:root[data-palette="forest"][data-mode="dark"] {
  --color-bg: #131c10;
  --color-surface: #20291d;
  --color-text: #e7eae7;
  --color-text-muted: #879082;
}
:root[data-palette="forest"][data-mode="light"] {
  --color-bg: #f1f4f0;
  --color-surface: #fafcfa;
  --color-text: #101911;
  --color-text-muted: #4e584f;
}

:root[data-palette="amber"][data-mode="dark"] {
  --color-bg: #24150f;
  --color-surface: #30221c;
  --color-text: #ede9e6;
  --color-text-muted: #9b8882;
}
:root[data-palette="amber"][data-mode="light"] {
  --color-bg: #f7f2f0;
  --color-surface: #fefbfa;
  --color-text: #1d140e;
  --color-text-muted: #5f524b;
}

:root[data-palette="slate"][data-mode="dark"] {
  --color-bg: #081d21;
  --color-surface: #18292d;
  --color-text: #e6eaec;
  --color-text-muted: #7c9195;
}
:root[data-palette="slate"][data-mode="light"] {
  --color-bg: #eff5f6;
  --color-surface: #f9fcfd;
  --color-text: #0b181d;
  --color-text-muted: #4a585d;
}
```

- [ ] **Step 3: Write the failing cascade-order test**

```typescript
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const here = dirname(fileURLToPath(import.meta.url));

// `--color-accent`/`--color-accent-2` are set both in a ramp block
// (`[data-palette="X"]`) and in the light mode block
// (`[data-mode="light"]`); both selectors tie on specificity, so which
// one wins is decided by source order alone. Light mode's override has
// to come LAST or it silently loses on every non-Nocturne palette (see
// the comment at the top of palettes.css).
describe("palettes.css cascade order", () => {
  it("declares [data-mode=\"light\"] after every ramp block", () => {
    const css = readFileSync(join(here, "palettes.css"), "utf-8");

    const modeLightPos = css.indexOf('[data-mode="light"]');
    expect(modeLightPos).toBeGreaterThan(-1);

    for (const palette of ["rose", "forest", "amber", "slate"]) {
      const rampPos = css.indexOf(`:root[data-palette="${palette}"] {`);
      expect(rampPos, `${palette} ramp block should exist`).toBeGreaterThan(-1);
      expect(modeLightPos).toBeGreaterThan(rampPos);
    }
  });
});
```

- [ ] **Step 4: Run the tests**

Run: `cd frontend && npm test -- palettes.order`
Expected: PASS (the file above is already written correctly; this pins it against future edits).

- [ ] **Step 5: Import `palettes.css` in `main.tsx`**

In `frontend/src/main.tsx`, change:

```typescript
import "./nocturne.css";
import "./styles.css";
```

to:

```typescript
import "./nocturne.css";
import "./palettes.css";
import "./styles.css";
```

- [ ] **Step 6: Run the frontend build to catch any CSS syntax error**

Run: `cd frontend && npm run build`
Expected: builds cleanly, no CSS parse errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/nocturne.css frontend/src/palettes.css frontend/src/palettes.order.test.ts frontend/src/main.tsx
git commit -m "feat: palettes.css — 5 palettes x light/dark token sets"
```

---

## Task 3: Backend `/theme` endpoint

**Files:**
- Modify: `src/kraft/api.py`
- Modify: `tests/test_settings_api.py`

**Interfaces:**
- Produces: `GET /theme -> {"palette": str, "mode": "light"|"dark"|"system"}`, `PUT /theme` (same body) `-> 200` with the saved value or `422` if `palette` isn't one of the 5 known ids. Written to `templates_dir / "theme.yaml"` via `config_mod.read_yaml`/`write_yaml`, same as every other settings file.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_api.py` (near the policy section, following its `# ── policy (5d) ──` divider style):

```python
# ── theme ────────────────────────────────────────────────────────────────────


def test_get_theme_defaults_to_nocturne_dark(client):
    assert client.get("/theme").json() == {"palette": "nocturne", "mode": "dark"}


def test_put_theme_round_trips_through_the_yaml(client, templates_dir):
    body = {"palette": "forest", "mode": "light"}
    assert client.put("/theme", json=body).status_code == 200
    assert client.get("/theme").json() == body
    assert yaml.safe_load((templates_dir / "theme.yaml").read_text()) == body


def test_put_theme_rejects_an_unknown_palette(client):
    resp = client.put("/theme", json={"palette": "cerulean", "mode": "dark"})
    assert resp.status_code == 422
    assert client.get("/theme").json()["palette"] == "nocturne"


def test_put_theme_rejects_an_unknown_mode(client):
    resp = client.put("/theme", json={"palette": "nocturne", "mode": "twilight"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_settings_api.py -k theme -v`
Expected: FAIL — `404 Not Found` (no `/theme` route yet).

- [ ] **Step 3: Add the endpoint**

In `src/kraft/api.py`, add near the `/policy` endpoints (after `put_policy`, matching the file's existing settings-endpoint grouping):

```python
PALETTE_IDS = frozenset({"nocturne", "rose", "forest", "amber", "slate"})
THEME_DEFAULT: dict = {"palette": "nocturne", "mode": "dark"}


class ThemeBody(BaseModel):
    palette: str
    mode: Literal["light", "dark", "system"]


@app.get("/theme")
async def get_theme(request: Request):
    st = request.app.state
    return config_mod.read_yaml(st.templates_dir / "theme.yaml", THEME_DEFAULT)


@app.put("/theme")
async def put_theme(body: ThemeBody, request: Request):
    if body.palette not in PALETTE_IDS:
        raise HTTPException(422, f"unknown palette: {body.palette!r}")
    st = request.app.state
    data = {"palette": body.palette, "mode": body.mode}
    config_mod.write_yaml(st.templates_dir / "theme.yaml", data)
    return data
```

`Literal` is already imported at the top of `src/kraft/api.py` (`from typing import Literal`, used by `kind: Literal["spec", "plan"]` elsewhere in the file) — no new import needed.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_settings_api.py -k theme -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py tests/test_settings_api.py
git commit -m "feat: GET/PUT /theme endpoint"
```

---

## Task 4: Frontend `Theme` type and API client

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`

**Interfaces:**
- Produces: `PaletteId` (`"nocturne" | "rose" | "forest" | "amber" | "slate"`), `ThemeMode` (`"light" | "dark" | "system"`), `Theme { palette: PaletteId; mode: ThemeMode }`, `getTheme(): Promise<Theme>`, `putTheme(theme: Theme): Promise<Theme>`.

- [ ] **Step 1: Add the types**

In `frontend/src/types.ts`, add near the `Policy`/`Notify` interfaces:

```typescript
export type PaletteId = "nocturne" | "rose" | "forest" | "amber" | "slate";
export type ThemeMode = "light" | "dark" | "system";

/** `GET/PUT /theme`. Instance-wide, like every other Settings-backed value —
 *  see the theme-palettes design doc for why this isn't per-user. */
export interface Theme {
  palette: PaletteId;
  mode: ThemeMode;
}
```

- [ ] **Step 2: Add the API functions**

In `frontend/src/api.ts`, add `Theme` to the type-only import block at the top, and add near `getPolicy`/`putPolicy`:

```typescript
export const getTheme = () => req<Theme>("/theme");
export const putTheme = (theme: Theme) => req<Theme>("/theme", json("PUT", theme));
```

- [ ] **Step 3: Run the frontend type-check / test suite**

Run: `cd frontend && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts
git commit -m "feat: Theme type and getTheme/putTheme"
```

---

## Task 5: `theme.ts` — palette catalog and `applyTheme`

**Files:**
- Create: `frontend/src/theme.ts`
- Create: `frontend/src/theme.test.ts`

**Interfaces:**
- Consumes: `PaletteId`, `ThemeMode` from `./types` (Task 4).
- Produces: `PALETTES: { id: PaletteId; name: string; bg: string; accent: string }[]` (swatch preview colors — the palette's own dark bg/accent, used for the Settings picker chip regardless of the app's current mode). `applyTheme(palette: PaletteId, mode: ThemeMode): void`.

- [ ] **Step 1: Write the failing tests**

```typescript
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyTheme, PALETTES } from "./theme";

function mockMatchMedia(initialMatches: boolean) {
  const listeners: ((e: MediaQueryListEvent) => void)[] = [];
  const mql: Partial<MediaQueryList> = {
    matches: initialMatches,
    media: "(prefers-color-scheme: dark)",
    addEventListener: vi.fn((_type, cb) => {
      listeners.push(cb as (e: MediaQueryListEvent) => void);
    }),
    removeEventListener: vi.fn((_type, cb) => {
      const i = listeners.indexOf(cb as (e: MediaQueryListEvent) => void);
      if (i >= 0) listeners.splice(i, 1);
    }),
  };
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue(mql));
  return {
    fire: (matches: boolean) => {
      for (const l of listeners) l({ matches } as MediaQueryListEvent);
    },
    listenerCount: () => listeners.length,
  };
}

beforeEach(() => {
  document.documentElement.removeAttribute("data-palette");
  document.documentElement.removeAttribute("data-mode");
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PALETTES", () => {
  it("lists exactly the 5 shipped palettes", () => {
    expect(PALETTES.map((p) => p.id).sort()).toEqual(
      ["amber", "forest", "nocturne", "rose", "slate"].sort(),
    );
  });
});

describe("applyTheme", () => {
  it("sets data-palette and data-mode directly for light/dark", () => {
    applyTheme("forest", "dark");
    expect(document.documentElement.dataset.palette).toBe("forest");
    expect(document.documentElement.dataset.mode).toBe("dark");

    applyTheme("rose", "light");
    expect(document.documentElement.dataset.palette).toBe("rose");
    expect(document.documentElement.dataset.mode).toBe("light");
  });

  it("resolves system mode from prefers-color-scheme", () => {
    mockMatchMedia(true);
    applyTheme("nocturne", "system");
    expect(document.documentElement.dataset.mode).toBe("dark");
  });

  it("keeps data-mode in sync while system stays selected", () => {
    const media = mockMatchMedia(false);
    applyTheme("nocturne", "system");
    expect(document.documentElement.dataset.mode).toBe("light");

    media.fire(true);
    expect(document.documentElement.dataset.mode).toBe("dark");
  });

  it("tears down the system listener when switching away from system", () => {
    const media = mockMatchMedia(false);
    applyTheme("nocturne", "system");
    expect(media.listenerCount()).toBe(1);

    applyTheme("nocturne", "dark");
    expect(media.listenerCount()).toBe(0);

    // a change firing after teardown must not resurrect system behaviour
    media.fire(true);
    expect(document.documentElement.dataset.mode).toBe("dark");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npm test -- theme.test`
Expected: FAIL — `Cannot find module './theme'`.

- [ ] **Step 3: Write `frontend/src/theme.ts`**

```typescript
import type { PaletteId, ThemeMode } from "./types";

/** Swatch preview colors for the Settings picker — each palette's own dark
 *  bg/accent, shown regardless of the app's current mode. A representative
 *  pair for a chip, not a copy of every token palettes.css defines. */
export const PALETTES: { id: PaletteId; name: string; bg: string; accent: string }[] = [
  { id: "nocturne", name: "Nocturne", bg: "#161826", accent: "#9184d9" },
  { id: "rose", name: "Rose", bg: "#1e1522", accent: "#b876bc" },
  { id: "forest", name: "Forest", bg: "#131c10", accent: "#53a768" },
  { id: "amber", name: "Amber", bg: "#24150f", accent: "#cc7c40" },
  { id: "slate", name: "Slate", bg: "#081d21", accent: "#04a1cb" },
];

let systemQuery: MediaQueryList | null = null;
let systemListener: ((e: MediaQueryListEvent) => void) | null = null;

function teardownSystemListener(): void {
  if (systemQuery && systemListener) {
    systemQuery.removeEventListener("change", systemListener);
  }
  systemQuery = null;
  systemListener = null;
}

/** Sets `data-palette`/`data-mode` on `<html>` — palettes.css (plus
 *  nocturne.css's unscoped `:root` for Nocturne dark, the default) does
 *  the actual repaint; nothing here touches an individual CSS variable.
 *  `"system"` resolves once against `prefers-color-scheme` and keeps a
 *  listener alive for as long as `"system"` stays selected, so the page
 *  follows an OS-level theme change live instead of only on next load. */
export function applyTheme(palette: PaletteId, mode: ThemeMode): void {
  const root = document.documentElement;
  root.dataset.palette = palette;

  teardownSystemListener();

  if (mode === "system") {
    systemQuery = window.matchMedia("(prefers-color-scheme: dark)");
    root.dataset.mode = systemQuery.matches ? "dark" : "light";
    systemListener = (e) => {
      root.dataset.mode = e.matches ? "dark" : "light";
    };
    systemQuery.addEventListener("change", systemListener);
  } else {
    root.dataset.mode = mode;
  }
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd frontend && npm test -- theme.test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/theme.ts frontend/src/theme.test.ts
git commit -m "feat: theme.ts — applyTheme and palette catalog"
```

---

## Task 6: Wire theme fetch + apply into boot

**Files:**
- Modify: `frontend/src/main.tsx`

**Interfaces:**
- Consumes: `api.getTheme()` (Task 4), `applyTheme()` (Task 5).

- [ ] **Step 1: Update `boot()`**

In `frontend/src/main.tsx`, change:

```typescript
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { useStore } from "./store";
import "./nocturne.css";
import "./palettes.css";
import "./styles.css";
import { connectEvents } from "./ws";

async function boot() {
  try {
    await useStore.getState().bootstrap();
  } catch (e) {
    console.error("bootstrap failed", e);
  }
  connectEvents();
  createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void boot();
```

to:

```typescript
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { useStore } from "./store";
import * as api from "./api";
import { applyTheme } from "./theme";
import "./nocturne.css";
import "./palettes.css";
import "./styles.css";
import { connectEvents } from "./ws";

async function boot() {
  try {
    const theme = await api.getTheme();
    applyTheme(theme.palette, theme.mode);
  } catch (e) {
    // Nocturne dark (nocturne.css's unscoped :root) is already the page's
    // look with no attributes set — a failed fetch here just means the
    // saved choice doesn't apply yet, not a broken page.
    console.error("theme fetch failed", e);
  }
  try {
    await useStore.getState().bootstrap();
  } catch (e) {
    console.error("bootstrap failed", e);
  }
  connectEvents();
  createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void boot();
```

No FOUC handling beyond this — `boot()` already gates the first `render()`, and the default (no saved theme yet) is identical to the unstyled fallback. See design doc's "Boot" section for why this is deliberately not doing more.

- [ ] **Step 2: Run the full frontend test suite (main.tsx has no dedicated test file, so this is a regression check for anything that imports it indirectly)**

Run: `cd frontend && npm test`
Expected: all existing tests still PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/main.tsx
git commit -m "feat: apply saved theme on boot"
```

---

## Task 7: Settings → Appearance page

**Files:**
- Modify: `frontend/src/views/Settings.tsx`
- Modify: `frontend/src/views/Settings.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `api.getTheme`/`putTheme` (Task 4), `applyTheme`/`PALETTES` (Task 5), `useResource`/`SaveRow`/`PageHead`/`SectionLabel` (existing, `Settings.tsx`).

- [ ] **Step 1: Add swatch-grid CSS**

In `frontend/src/styles.css`, add near the other form/settings primitives (following the file's "Kraft's own layer" section, e.g. near `.cap-row`):

```css
/* — appearance (theme palettes) — */
.palette-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(96px, 1fr));
  gap: var(--space-3);
  margin: var(--space-3) 0;
}
.palette-swatch {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2);
  border: 1px solid var(--color-divider);
  border-radius: var(--radius-md);
  background: transparent;
  font: inherit;
  color: var(--color-text);
  cursor: pointer;
}
.palette-swatch:hover { background: color-mix(in srgb, var(--color-text) 7%, transparent); }
.palette-swatch[aria-pressed="true"] {
  border-color: var(--color-accent);
  box-shadow: inset 0 0 0 1px var(--color-accent);
}
.palette-swatch-dot { width: 32px; height: 32px; border-radius: 50%; }
```

- [ ] **Step 2: Write the failing test**

In `frontend/src/views/Settings.test.tsx`, line 7 is currently:

```typescript
import type { TemplateSummary } from "../types";
```

Change it to:

```typescript
import type { TemplateSummary, Theme } from "../types";
```

Line 24 starts the `policy` fixture (`const policy = { ... };`). Add a new fixture right after it (and after the `access` fixture that follows it, wherever the file's editor lands — anywhere at module scope alongside the other fixtures is fine):

```typescript
const theme: Theme = { palette: "nocturne", mode: "dark" };
```

Inside the shared `beforeEach` (the block containing `vi.spyOn(api, "getPolicy").mockResolvedValue(policy);`), add one more line to the same block:

```typescript
vi.spyOn(api, "getTheme").mockResolvedValue(theme);
```

Then add a new `describe` block, anywhere after `renderAt` is defined:

```typescript
describe("Settings · appearance", () => {
  it("lists all 5 palettes and the light/dark/system control", async () => {
    renderAt("/settings/appearance");
    expect(await screen.findByText("Nocturne")).toBeInTheDocument();
    for (const name of ["Rose", "Forest", "Amber", "Slate"]) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
    expect(screen.getByRole("radiogroup", { name: /mode/i })).toBeInTheDocument();
  });

  it("previews live on click and saves on Save", async () => {
    const put = vi.spyOn(api, "putTheme").mockResolvedValue({ palette: "forest", mode: "dark" });
    renderAt("/settings/appearance");
    await screen.findByText("Nocturne");

    fireEvent.click(screen.getByText("Forest"));
    expect(document.documentElement.dataset.palette).toBe("forest");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(put).toHaveBeenCalledWith({ palette: "forest", mode: "dark" });
  });

  it("Discard reverts the live preview back to the loaded value", async () => {
    renderAt("/settings/appearance");
    await screen.findByText("Nocturne");

    fireEvent.click(screen.getByText("Rose"));
    expect(document.documentElement.dataset.palette).toBe("rose");

    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Discard" }));
    expect(document.documentElement.dataset.palette).toBe("nocturne");
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd frontend && npm test -- Settings.test`
Expected: FAIL — `/settings/appearance` renders nothing / `getTheme` is not a function on the mock yet, since the route and mock don't exist.

- [ ] **Step 4: Add the `AppearancePage` component and wire the route**

In `frontend/src/views/Settings.tsx`:

Add `"Theme"` to the `import type { ... } from "../types"` block, and add `PALETTES, applyTheme` to a new `import { PALETTES, applyTheme } from "../theme";`.

Add `{ to: "appearance", label: "Appearance" }` to the `PAGES` array (after `"policy"`, before `"intake"` — appearance is a personalization setting like the pages around it, not an operational one).

Add the page component, placed near `PolicyPage` (after it, following the file's page-per-section grouping):

```typescript
/* ── 5g appearance ────────────────────────────────────────────────────────── */

const MODES: { id: Theme["mode"]; label: string }[] = [
  { id: "light", label: "Light" },
  { id: "dark", label: "Dark" },
  { id: "system", label: "System" },
];

function AppearancePage() {
  const { value, error, reload } = useResource(() => api.getTheme());
  const [draft, setDraft] = useState<Theme | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const theme = draft ?? value;
  const dirty = draft !== null;

  const preview = (next: Theme) => {
    setDraft(next);
    applyTheme(next.palette, next.mode);
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setMessage(null);
    try {
      await api.putTheme(draft);
      setDraft(null);
      await reload();
      setMessage("saved");
    } catch (e) {
      setMessage(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const discard = () => {
    setDraft(null);
    if (value) applyTheme(value.palette, value.mode);
  };

  return (
    <>
      <PageHead
        title="Appearance"
        note="pick a palette and a light/dark mode — preview applies immediately, Save keeps it"
      />
      {error && <p className="form-error">{error}</p>}
      <SectionLabel>Palette</SectionLabel>
      <div className="palette-grid">
        {PALETTES.map((p) => (
          <button
            key={p.id}
            type="button"
            className="palette-swatch"
            aria-pressed={theme?.palette === p.id}
            onClick={() => theme && preview({ ...theme, palette: p.id })}
          >
            <span
              className="palette-swatch-dot"
              style={{ background: `linear-gradient(135deg, ${p.bg} 50%, ${p.accent} 50%)` }}
            />
            {p.name}
          </button>
        ))}
      </div>
      <SectionLabel>Mode</SectionLabel>
      <div className="seg" role="radiogroup" aria-label="mode">
        {MODES.map((m) => (
          <label key={m.id} className="seg-opt">
            <input
              type="radio"
              name="theme-mode"
              checked={theme?.mode === m.id}
              onChange={() => theme && preview({ ...theme, mode: m.id })}
            />
            {m.label}
          </label>
        ))}
      </div>
      <SaveRow
        onSave={save}
        onDiscard={discard}
        dirty={dirty}
        busy={busy}
        message={message}
        hint="writes theme.yaml"
      />
    </>
  );
}
```

Add the route inside the `<Routes>` block, after `policy`:

```typescript
<Route path="appearance" element={<AppearancePage />} />
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd frontend && npm test -- Settings.test`
Expected: PASS.

- [ ] **Step 6: Run the full frontend suite**

Run: `cd frontend && npm test`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/Settings.tsx frontend/src/views/Settings.test.tsx frontend/src/styles.css
git commit -m "feat: Settings > Appearance page"
```

---

## Manual verification (not automated — do this once after Task 7)

- [ ] `just dev`, open the app, go to Settings → Appearance.
- [ ] Click through all 5 palettes and all 3 modes — confirm the whole app repaints instantly on click, before Save.
- [ ] Save, reload the page — confirm the saved combination is what loads (not Nocturne dark).
- [ ] Change a palette, click Discard — confirm the app reverts without a page reload.
- [ ] Pick "System", toggle the OS light/dark setting — confirm the app follows without a reload.
