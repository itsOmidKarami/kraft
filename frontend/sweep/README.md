# Kraft UI sweep

Screenshots + machine checks for every screen × data variant × viewport × shell state, against **mocked** `/api/**` so every display state (rate_limited, capped, budget, escalated, archived…) is reachable in milliseconds. No orchestrator, no fake agent.

## Install (once)

```bash
cd frontend && npx playwright install chromium
```

The harness is versioned here. `vite.config.ts` excludes `sweep/**` from vitest, so `npm test` does not pick up the Playwright specs.

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

`clippedEllipsis` skips an element carrying `data-allow-ellipsis`: a deliberate one-line cut with the whole text in its `title`. Only the element carrying it (W10.D). Do not use this attribute anywhere else — it is allowed on exactly these seven, each with its own `checks.spec.ts` case:

1. `.doc-path` — a document's path, cut from the left (W10.D in the Documents list; since W12.2 the document pane header's path line)
2. `.detail-meta-part` — the item header's meta line (W11 · A.1)
3. `.board-row-title` — the board row title (W11 · B.2)
4. `.board-row-meta` — the board row meta line (W11 · B.2)
5. the peek header's id / meta line (W11 · I)
6. `.app-header-crumb-current` — the item title crumb in the app header (W12.1)
7. `.doc-modal-name` — the document pane header's title (W12.2)

The element must carry its full text in `title`. Anything else that ellipsizes still fails the check; a new use needs a decision first.

## Axes

- Widths: 390, 768, 1024, 1100, 1280, 1440, 1920 (+1280×700).
- Data: `default`, `long` (140-char titles, 32-hex ids in text, 12 repos, 40-node timelines, 600-line logs, 40-file diffs), `many` (60 rows), `empty`.
- Shell (at 1280): sidebar open / rail, light mode, comfortable density, short viewport, group-by repo/template.
- Screens: board, board+peek (5 states), archived, item ×14 states (default + long), 5 inspector tabs (default/long/scrolled), log maximized, 7 composers (empty + filled), intake modal, search (overlay/page/viewer), analytics, 9 settings pages (+ sub-states), login.

## Baseline

`e2e-shots/sweep/` is not tracked (`frontend/e2e-shots/` is gitignored). It is the local baseline, and it is only as fresh as the last run. Take one on `main` after a merge, before touching UI:

```bash
cd frontend
node sweep/wave.mjs all --baseline     # full sweep, snapshot → e2e-shots/baseline/all/
```

## Before an MR

```bash
node sweep/wave.mjs all                # re-shoot, pixel-diff against the baseline, rules → e2e-shots/DIFF-all.md
```

`all` has one rule: no newly flagged cells. A cell with no flags in the baseline (`nested-scroll` aside) must not gain any. Read `DIFF-all.md` and look at the PNGs under "Regressions" and "Still flagged": a passing rule is not a passing change. `node sweep/wave.mjs <Wn>` runs one wave's screens with that rule plus the wave's own rules from `waves.json` (`no-flag` for `offscreen`, `clipped-v`, `target<44`; `no-console`; `flow-completes`; …).

When a case records `setupError`, fix the selector or fixture in `sweep/`, never `src/`. The spec never asserts; a red test means the harness threw.

## History and briefs

- `sweep/HISTORY.md`: the receipts of W0–W14. For each wave: its rules, the cells it changed, cleared or regressed, its commits, open questions and MRs. A new wave appends here.
- `sweep/briefs/`: the one home for wave briefs (`W<n>_BRIEF.md`). It also holds `PUNCHLIST-v3.md`, the evidence list the waves closed, and `QUESTIONS.md`, where a wave writes a decision it cannot make. A new brief goes here in the same MR as its wave; the design handoff links here and does not copy it. Feature specs are not briefs: they follow CLAUDE.md and go to Kraft as work-item attachments. `sweep/WAVES.md` is the W0–W9 plan and the loop every wave follows.
- `design/handoff_v4/`: the rules the code follows, with sweep frames as the reference screens.

## Accepted flags

These stay flagged on purpose; do not "fix" them:

- `nested-scroll` = 2 on item pages: the inspector and the right pane are two independent scrollers (the model).
- `console` on `login/`: the 401 before sign-in, until the backend's `authenticated` field lands (Kraft-yx79s).
- `ellipsis` on the `data-allow-ellipsis` cells listed above. Only those seven elements may carry the attribute.
