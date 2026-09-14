# Kraft UI sweep

Screenshots + machine checks for every screen × data variant × viewport × shell state, against **mocked** `/api/**` so every display state (rate_limited, capped, budget, escalated, archived…) is reachable in milliseconds. No orchestrator, no fake agent.

## Install (once)

```bash
# from the Kraft repo root
mkdir -p frontend/sweep
cp <design-export>/sweep/{fixtures.ts,mockApi.ts,checks.ts,sweep.spec.ts,collect.mjs,playwright.sweep.config.ts} frontend/sweep/
cd frontend && npx playwright install chromium
```

`vite.config.ts` excludes `e2e/**` from vitest; add `"sweep/**"` to that `exclude` list so `npm test` does not pick the spec up.

## Run

```bash
cd frontend
npx playwright test -c sweep/playwright.sweep.config.ts        # ~350 shots, 4 workers, ~6–10 min
node sweep/collect.mjs                                           # → e2e-shots/sweep/manifest.json + FINDINGS.md
```

Subsets: `SWEEP_SCREEN=board,item-changes SWEEP_WIDTHS=390,1280 SWEEP_VARIANT=long npx playwright test -c sweep/playwright.sweep.config.ts`

## Output

```
e2e-shots/sweep/
  manifest.jsonl          one line per shot (appended; re-runs override by id)
  manifest.json           collected, with flags
  FINDINGS.md             tallies, worst screens, width-sensitivity table
  <screen>/<variant>@<width>[~shell].png
```

Per shot: `pageOverflowX`, `offscreenRight`, `clippedEllipsis`, `clippedVertical`, `smallTargets` (<44px, phone), `smallInputs` (<16px, phone), `nestedScrollers`, `consoleErrors`, `chromeMoved` (header/action bar/tabs moved after scroll), `setupError`.

### `data-allow-ellipsis` — an allowlist, not a style

`clippedEllipsis` skips an element carrying `data-allow-ellipsis`: a deliberate one-line cut with the whole text in its `title`. Only the element carrying it (W10.D). Do not use this attribute anywhere else — it is allowed on exactly these five, each with its own `checks.spec.ts` case:

1. `.doc-path` — the Documents list path (W10.D; unused since W11 · G took the path out of the row)
2. `.detail-meta-part` — the item header's meta line (W11 · A.1)
3. `.board-row-title` — the board row title (W11 · B.2)
4. `.board-row-meta` — the board row meta line (W11 · B.2)
5. the peek header's id / meta line (W11 · I)

The element must carry its full text in `title`. Anything else that ellipsizes still fails the check; a new use needs a decision first.

## Axes

- Widths: 390, 768, 1024, 1100, 1280, 1440, 1920 (+1280×700).
- Data: `default`, `long` (140-char titles, 32-hex ids in text, 12 repos, 40-node timelines, 600-line logs, 40-file diffs), `many` (60 rows), `empty`.
- Shell (at 1280): sidebar open / rail, light mode, comfortable density, short viewport, group-by repo/template.
- Screens: board, board+peek (5 states), archived, item ×14 states (default + long), 5 inspector tabs (default/long/scrolled), log maximized, 7 composers (empty + filled), intake modal, search (overlay/page/viewer), analytics, 9 settings pages (+ sub-states), login.

## Reviewing

Copy `e2e-shots/sweep/` into the design project as `sweep-shots/` and open `Kraft Sweep.dc.html` — contact sheet, flags, triage, punch-list export.

## Rules for whoever runs it

- Fix **selectors and fixtures** in `sweep/` when a case records `setupError`; do not touch `src/` to make a shot work.
- The spec never asserts. A red test means the harness itself threw — read the stack, not the UI.
