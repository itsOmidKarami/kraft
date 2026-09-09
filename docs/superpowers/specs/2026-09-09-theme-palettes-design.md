# Theme palettes — design

## Problem

The frontend has exactly one look: Nocturne, a dark blurple theme hardcoded
as literal hex values in `frontend/src/nocturne.css`'s `:root`. There is no
mechanism to change it — no light mode, no alternate accent, nothing
user-selectable. Users want a choice of color palettes, with a live preview
before committing.

## Scope

- 5 curated palettes (Nocturne + 4 new hues), each hand-tuned in OKLCH the
  way Nocturne's own accent was — same lightness/chroma, different hue — so
  documented contrast ratios carry over without re-deriving from scratch.
- A separate Light / Dark / System toggle, orthogonal to palette choice.
  Every palette defines both a light and a dark token set.
- Instance-wide setting (matches every other Settings page — none are
  per-user or per-repo today).
- Live preview: selecting a swatch or mode re-paints the running app
  immediately; Save persists it, Discard reverts it.

Out of scope for v1 (deliberately, not silently dropped):
- Per-user themes — Settings has no per-user model to hang it on today.
- Arbitrary/custom hue picker — 5 curated palettes only.
- Automated contrast-audit tooling — verified manually per combo, the way
  Nocturne's existing token comments document their own ratios.

## Mechanism

CSS attributes, not JS-computed variables. `<html>` carries
`data-palette="<id>"` and `data-mode="light"|"dark"` attributes. With no
attributes set, the app renders exactly as it does today — Nocturne dark is
the zero-risk default via `nocturne.css`'s existing unscoped `:root` block.

A new `frontend/src/palettes.css`, imported after `nocturne.css` and before
`styles.css`, defines one override block per (palette, mode) combination
other than the default:

```css
:root[data-palette="rose"][data-mode="dark"] {
  --color-accent: ...; --color-accent-2: ...;
  --color-bg: ...; --color-surface: ...; --color-text: ...;
  --color-divider: ...; --color-neutral-100..900: ...;
  --color-accent-100..900: ...; --color-accent-2-100..900: ...;
  --shadow-sm/md/lg: ...;
}
:root[data-palette="rose"][data-mode="light"] { ... }
```

5 palettes × 2 modes − 1 (Nocturne dark, already `:root`) = 9 blocks. Each
restates every token Nocturne's `:root` defines. Light-mode blocks need a
genuinely light bg/surface/text/shadow set, not just a lightened accent —
this is real per-palette design work, contrast-checked the way Nocturne's
own header comments already document (WCAG 1.4.3, text-on-bg and
text-on-surface, at minimum).

`frontend/src/theme.ts` (new, small):
- `PALETTES`: catalog of `{ id, name, previewColors }` for rendering
  swatches in Settings — a compact representative set (e.g. bg/surface/
  accent/text), not a mirror of every CSS token.
- `applyTheme(palette: PaletteId, mode: "light" | "dark" | "system")`: sets
  `data-palette` on `<html>`; resolves `"system"` via
  `matchMedia('(prefers-color-scheme: dark)')` to an actual `data-mode` of
  `light`/`dark`, and installs a change listener for as long as `"system"`
  stays selected (torn down when the mode changes away from it).

## Persistence

Mirrors `policy.py`/`PolicyBody` exactly:

- `theme.yaml` in `templates_dir`: `{palette: str, mode: "light"|"dark"|"system"}`.
- `GET /theme` / `PUT /theme` in `api.py`, using `config_mod.read_yaml` /
  `write_yaml`, same shape as the existing `/policy` pair.
- `frontend/src/api.ts`: `getTheme()` / `putTheme()`.
- `frontend/src/types.ts`: `Theme` type.

## Boot

`main.tsx`'s `boot()` already `await`s `useStore.getState().bootstrap()`
before the first `render()` — nothing paints before that resolves. Fetch and
`applyTheme()` the saved theme there too, alongside the existing bootstrap
call. No localStorage/flash-of-wrong-theme handling for v1 — skipped
deliberately; add if a flash is actually reported once this ships.

## Settings UI

New `"Appearance"` entry in `Settings.tsx`'s `PAGES`, built on the same
`useResource` + `SaveRow` (dirty/discard) pattern every other Settings page
already uses (see `Policy`/`Steering` pages for the shape).

Body: 5 swatch chips (from `PALETTES`, `theme.ts`) + a `.seg` control
(existing Nocturne component) for Light/Dark/System. Clicking either calls
`applyTheme()` immediately — real live preview against the running app, not
a static mock — and marks the form dirty. Save calls `putTheme()`. Discard
reverts both the form state and the applied attributes back to the loaded
value (calls `applyTheme()` again with the loaded palette/mode, since the
live preview already changed the DOM).

## Testing

- Backend: `GET`/`PUT /theme` round-trip test, mirroring the existing
  `/policy` test.
- Frontend: unit test for `theme.ts` (attribute-setting, `"system"`
  resolution and listener teardown).
- `Settings.test.tsx`: Appearance page renders the 5×3 grid, Save calls the
  API with the selected values, Discard reverts applied attributes.

## Risks / open questions carried into implementation

- The 9 new (palette, mode) token blocks are real design work, not
  mechanical derivation — each needs its own contrast pass. Implementation
  should treat "produce and verify one palette's dark+light token set" as
  its own repeatable step, not a single one-shot for all 5.
