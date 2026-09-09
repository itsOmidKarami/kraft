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
    lms_l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = _cbrt(lms_l), _cbrt(m), _cbrt(s)
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
    lms_l, m, s = l_**3, m_**3, s_**3
    r = +4.0767416621 * lms_l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * lms_l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * lms_l - 0.7034186147 * m + 1.7076147010 * s
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
    lms_l, m, s = l_**3, m_**3, s_**3
    r = +4.0767416621 * lms_l - 3.3077115913 * m + 0.2309699292 * s
    g = -1.2684380046 * lms_l + 2.6097574011 * m - 0.3413193965 * s
    b = -0.0041960863 * lms_l - 0.7034186147 * m + 1.7076147010 * s
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
