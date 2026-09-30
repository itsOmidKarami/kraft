"""Generates `frontend/src/ng/theme/theme.css`, the new UI's colour tokens.

A port of `make()` in the UX V2 prototype's `kraft-themes.js`, with the roles
`kraft-look.js` `vars()` derives from it. Run by hand and check the output
in, like `palettes.css`:

    uv run python dev/gen_theme.py

`tests/test_theme_contrast.py` checks every combination this produces against
the contrast floors, and `ng/theme/theme.contrast.test.ts` checks the file on
disk, so the two cannot drift.

Where a combination misses a floor, `nudge` moves that role's lightness away
from its grounds in 0.005 steps and prints what it moved. The floors are the
fixed thing; the prototype's lightness ladder is where the slack goes.
"""

from __future__ import annotations

import math
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "ng" / "theme" / "theme.css"

MODES = ("dark", "light")
AMOUNTS = ("mono", "subtle", "full")
#: Chroma factor per amount (`K` in the prototype).
K = {"mono": 0.0, "subtle": 1.0, "full": 1.6}
#: Status chroma per amount.
STATUS_C = {"mono": 0.03, "subtle": 0.07, "full": 0.11}
#: id: (hue, base chroma, mono lightness offset `dl`). The prototype's
#: per-surface accent hue and chroma are left out: `vars()` only lets them
#: through at mono, where the accent is the text colour anyway.
SURFACES = {
    "graphite": (265, 0.003, 0.0),
    "slate": (235, 0.010, -0.03),
    "ink": (285, 0.012, -0.015),
    "sand": (70, 0.010, 0.015),
    "moss": (150, 0.010, 0.03),
}
#: Accent hues (`ACC` in `kraft-look.js`); `none` is the text colour.
ACCENTS = {"none": None, "blue": 250, "violet": 285, "green": 155, "amber": 75, "rose": 350}
ACCENT_C = 0.07

#: Contrast floors: role -> (minimum, grounds it is read on).
TEXT_ON = ("bg", "side", "surface")
FLOORS = {
    "text": (4.5, TEXT_ON),
    "text-sub": (4.5, TEXT_ON),
    "text-muted": (4.5, TEXT_ON),
    "text-faint": (3.0, TEXT_ON),
    "focus": (3.0, ("bg",)),
}
#: Status colours are both a fill on the ground and text on a card.
STATUS = ("ok", "warn", "bad", "info")
STATUS_FLOORS = ((3.0, "bg"), (4.5, "surface"))
ACCENT_FLOOR = (4.5, ("bg", "surface"))


# ── OKLab / OKLCH <-> sRGB (Björn Ottosson, D65), as the prototype does it ──


def _to_srgb(x: float) -> float:
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def _to_linear(v: float) -> float:
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def oklab_hex(L: float, a: float, b: float) -> str:
    l_ = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3
    rgb = (
        4.0767416621 * l_ - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l_ + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l_ - 0.7034186147 * m + 1.707614701 * s,
    )
    # Math.round, not Python's banker's rounding, to match the prototype.
    return "#" + "".join(f"{math.floor(min(1, max(0, _to_srgb(v))) * 255 + 0.5):02x}" for v in rgb)


def oklch(L: float, C: float, h: float) -> tuple[float, float, float]:
    return (L, C * math.cos(math.radians(h)), C * math.sin(math.radians(h)))


def hex_oklab(hx: str) -> tuple[float, float, float]:
    r, g, b = (_to_linear(int(hx[i : i + 2], 16) / 255) for i in (1, 3, 5))
    l_ = math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b)
    m = math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b)
    s = math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b)
    return (
        0.2104542553 * l_ + 0.793617785 * m - 0.0040720468 * s,
        1.9779984951 * l_ - 2.428592205 * m + 0.4505937099 * s,
        0.0259040371 * l_ + 0.7827717662 * m - 0.808675766 * s,
    )


def mix(a: str, b: str, p: float) -> tuple[float, float, float]:
    """`color-mix(in oklab, a p%, b)`."""
    x, y = hex_oklab(a), hex_oklab(b)
    return tuple(p * i + (1 - p) * j for i, j in zip(x, y, strict=True))  # type: ignore[return-value]


def luminance(hx: str) -> float:
    r, g, b = (_to_linear(int(hx[i : i + 2], 16) / 255) for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# ── the model ──


def nudge(lab, grounds: list[str], floor: float, dark: bool, what: str, log: list[str]) -> str:
    """`lab` as hex, its lightness stepped away from `grounds` until it clears
    `floor` on every one of them."""
    L, a, b = lab
    start = L
    while min(contrast(oklab_hex(L, a, b), g) for g in grounds) < floor:
        L += 0.005 if dark else -0.005
        if not 0 <= L <= 1:
            raise SystemExit(f"{what}: no lightness clears {floor}")
    if L != start:
        log.append(f"{what}: L {start:.3f} -> {L:.3f}")
    return oklab_hex(L, a, b)


def neutrals(surface: str, mode: str, amount: str, log: list[str]) -> dict[str, str]:
    """Surface, text and status tokens for one `[data-surface][data-mode][data-amount]`."""
    h, c0, dl = SURFACES[surface]
    dark, k, sc = mode == "dark", K[amount], STATUS_C[amount]
    c = c0 * k
    d = dl * (1 if dark else 0.6) if amount == "mono" else 0.0
    g = lambda L, C: oklab_hex(*oklch(L + d, C, h))  # noqa: E731 - grounds take the mono offset
    if dark:
        t = {"side": g(0.2, c), "bg": g(0.25, c), "surface": g(0.305, c), "line": g(0.35, c)}
        t["border"] = g(0.44, c)
        text = {"text": (0.95, 0.25), "text-sub": (0.83, 0.4), "text-muted": (0.7, 0.6)}
        status = {"ok": (0.78, sc, 155), "warn": (0.8, sc, 85), "bad": (0.72, sc * 1.2, 20)}
        status["info"] = (0.78, sc, 250)
    else:
        t = {"side": g(0.915, c * 0.7), "bg": g(0.95, c * 0.6), "surface": g(0.985, c * 0.4)}
        t["line"], t["border"] = g(0.875, c * 0.7), g(0.79, c * 0.8)
        text = {"text": (0.2, 0.5), "text-sub": (0.33, 0.6), "text-muted": (0.47, 0.8)}
        status = {"ok": (0.48, sc, 155), "warn": (0.5, sc, 75), "bad": (0.5, sc * 1.2, 20)}
        status["info"] = (0.48, sc, 250)
    t["surface-2"] = oklab_hex(*mix(t["surface"], t["line"], 0.6))
    where = f"{surface}/{mode}/{amount}"
    for role, (L, cf) in text.items():
        floor, on = FLOORS[role]
        t[role] = nudge(
            oklch(L, c * cf, h), [t[x] for x in on], floor, dark, f"{where} {role}", log
        )
    floor, on = FLOORS["text-faint"]
    faint = mix(t["text-muted"], t["bg"], 0.55)
    t["text-faint"] = nudge(faint, [t[x] for x in on], floor, dark, f"{where} text-faint", log)
    for role, (L, C, hh) in status.items():
        for floor, on in STATUS_FLOORS:
            t[role] = nudge(oklch(L, C, hh), [t[on]], floor, dark, f"{where} {role}", log)
            L = hex_oklab(t[role])[0]
    # Decisions §13: selection, primary buttons and focus stay neutral.
    t["selection"] = t["focus"] = t["text"]
    # Review diff colours (Appearance › Review diff): theme = the status pair,
    # safe = blue and orange, plain = marks only, in the secondary text colour.
    t["diff-add-theme"], t["diff-del-theme"] = t["ok"], t["bad"]
    safe_l = 0.78 if dark else 0.5
    t["diff-add-safe"] = oklab_hex(*oklch(safe_l, 0.09, 250))
    t["diff-del-safe"] = oklab_hex(*oklch(safe_l, 0.09, 55))
    t["diff-add-plain"] = t["diff-del-plain"] = t["text-sub"]
    return t


def accent(name: str, mode: str, amount: str, grounds: list[str], log: list[str]) -> str:
    """The accent for `[data-accent][data-mode][data-amount]`: the same on every
    surface, so it has to clear the floor on all of their grounds."""
    hue = ACCENTS[name]
    if hue is None or amount == "mono":
        return "var(--text)"
    dark = mode == "dark"
    lab = oklch(0.78 if dark else 0.45, ACCENT_C * K[amount], hue)
    return nudge(lab, grounds, ACCENT_FLOOR[0], dark, f"accent {name}/{mode}/{amount}", log)


def generate() -> tuple[dict, dict, list[str]]:
    """(neutral blocks, accent blocks, what was nudged)."""
    log: list[str] = []
    blocks = {(s, m, a): neutrals(s, m, a, log) for s in SURFACES for m in MODES for a in AMOUNTS}
    accents = {}
    for m in MODES:
        for a in AMOUNTS:
            grounds = [blocks[s, m, a][g] for s in SURFACES for g in ACCENT_FLOOR[1]]
            for x in ACCENTS:
                accents[x, m, a] = accent(x, m, a, grounds, log)
    return blocks, accents, log


def render(blocks: dict, accents: dict) -> str:
    out = [
        "/* Generated by dev/gen_theme.py (a port of the UX V2 prototype's make()).",
        " * Do not edit: change the generator and run `uv run python dev/gen_theme.py`. */",
        '[data-mode="dark"] { color-scheme: dark; }',
        '[data-mode="light"] { color-scheme: light; }',
    ]
    for (s, m, a), t in blocks.items():
        body = " ".join(f"--{k}: {v};" for k, v in t.items())
        out.append(f'[data-surface="{s}"][data-mode="{m}"][data-amount="{a}"] {{ {body} }}')
    for (x, m, a), v in accents.items():
        out.append(f'[data-accent="{x}"][data-mode="{m}"][data-amount="{a}"] {{ --accent: {v}; }}')
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    blocks, accents, log = generate()
    OUT.write_text(render(blocks, accents))
    print(f"wrote {OUT} ({len(blocks)} neutral blocks, {len(accents)} accent blocks)")
    for line in log:
        print(f"  nudged {line}")
