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

### `offscreenRight` exemptions: scroll strips and `data-pan` canvases

`offscreenRight` exempts an element cut off by a sideways scroll strip (`overflow-x: auto | scroll`) that sits in the viewport, since scrolling reaches it. A graph canvas carrying `data-pan` gets the same treatment: it clips with `overflow: hidden`, but drag, scroll and fit reach what it cuts off, so its content is exempt while the canvas itself is in view; a canvas that runs past the edge still counts (R41, ux2-W3). Only `StageGraph` and `NodeGraph` under `src/ng/graph/` carry it, and `ng/css.contract.test.ts` fails if any other source file does: a page that needs a pannable canvas goes through a graph component. Both sides have a `checks.spec.ts` case.

### `data-allow-ellipsis` — an allowlist, not a style

`clippedEllipsis` skips an element carrying `data-allow-ellipsis`: a deliberate one-line cut with the whole text in its `title`. Only the element carrying it (W10.D). Do not use this attribute anywhere else — it is allowed on exactly these eleven, each with its own `checks.spec.ts` case:

1. `.doc-path` — a document's path, cut from the left (W10.D in the Documents list; since W12.2 the document pane header's path line)
2. `.detail-meta-part` — the item header's meta line (W11 · A.1)
3. `.board-row-title` — the board row title (W11 · B.2)
4. `.board-row-meta` — the board row meta line (W11 · B.2)
5. the peek header's id / meta line (W11 · I)
6. `.app-header-crumb-current` — the item title crumb in the app header (W12.1)
7. `.doc-modal-name` — the document pane header's title (W12.2)
8. `.ng-crumb-repo` — the repo crumb of the /ng header, which shrinks first (UX V2 W2, brief Decided 11)
9. `.ng-crumb-current` — the item title crumb of the /ng header, which shrinks last (UX V2 W2, brief Decided 11)
10. `.item-one-line` — the /ng item page's question banner in a node view, the question on one line (UX V2 W5, Decisions §4)
11. `.lib-name` — a component's name in the /ng Library list, which keeps its first 14 characters and cuts a longer id such as `never-signal-processes-you-didnt-start` (UX V2 W12, R10)

The element must carry its full text in `title`. Anything else that ellipsizes still fails the check; a new use needs a decision first.

## Axes

- Widths: 390, 768, 1024, 1100, 1280, 1440, 1920 (+1280×700).
- Data: `default`, `long` (140-char titles, 32-hex ids in text, 12 repos, 40-node timelines, 600-line logs, 40-file diffs), `many` (60 rows), `empty`.
- Shell (at 1280): sidebar open / rail, light mode, comfortable density, short viewport, group-by repo/template.
- Screens: board, board+peek (5 states), archived, item ×14 states (default + long), 5 inspector tabs (default/long/scrolled), log maximized, 7 composers (empty + filled), intake modal, search (overlay/page/viewer), analytics, 9 settings pages (+ sub-states), login.

## Baseline

`e2e-shots/sweep/` is not tracked (`frontend/e2e-shots/` is gitignored). It is the local baseline, and it is only as fresh as the last run. Every page's `Date` is frozen at `NG_NOW` (`installMocks`), so relative durations do not move with the wall clock; a baseline taken before that freeze differs once, in clock text, so retake it. Take one on `main` after a merge, before touching UI:

```bash
cd frontend
node sweep/wave.mjs all --baseline     # full sweep, snapshot → e2e-shots/baseline/all/
```

`wave.mjs` fails the run (exit 1, naming the spec) when a spec exits non-zero and wrote no manifest rows, because a spec that never loaded must not read as a pass; a spec that ran and recorded `setupError`s is still data. `just test-ui` type-checks every `sweep/**/*.ts`, so a syntax error in a spec fails there too.

`--baseline` shoots first when `e2e-shots/sweep/manifest.jsonl` does not exist (a fresh worktree), and otherwise snapshots the shots already there without shooting again.

## Before an MR

```bash
node sweep/wave.mjs all                # re-shoot, pixel-diff against the baseline, rules → e2e-shots/DIFF-all.md
```

`all` has one rule: no newly flagged cells. A cell with no flags in the baseline (`nested-scroll` aside) must not gain any. Read `DIFF-all.md` and look at the PNGs under "Regressions" and "Still flagged": a passing rule is not a passing change. `node sweep/wave.mjs <Wn>` runs one wave's screens with that rule plus the wave's own rules from `waves/<wave>.json` (`no-flag` for `offscreen`, `clipped-v`, `target<44`; `no-console`; `flow-completes`; …).

When a case records `setupError`, fix the selector or fixture in `sweep/`, never `src/`. The spec never asserts; a red test means the harness threw.

## UX V2 (`/ng`)

The new UI is served under `/ng` beside the shipped one. Its screens use the prefix `ng-` (`ng-shell`, `ng-board`, …) and `goto("/ng/...")`. Its waves are the files `waves/ux2-W<n>.json` (the plain `W0`–`W13` files are the finished fix programme), so a wave runs as `node sweep/wave.mjs ux2-W<n>` and writes `e2e-shots/DIFF-ux2-W<n>.md`.

W2 screens: `ng-shell` (frame, sidebar pinned/rail/revealed), `ng-search` (the ⌘K overlay), `ng-login` (sign-in; the mock's `login` option picks the 401 or 429 answer, and the clock is fixed so the countdown reads the same), `ng-firstrun` (the board with no repo; the page clock is installed after load so the probe rows' reveal is stepped by the cell), and the flows `flow-ng-search-keyboard`, `flow-ng-sidebar-pin` (1280) and `flow-ng-sidebar-rail` (1024). Flow steps assert; a failed assertion is that step's error, which `flow-completes` reports.

## Adding a wave's cases, flows and rules

A wave **adds files and edits no shared list**; `sweep.spec.ts`, `interactions.spec.ts` and `wave.mjs` name no wave.

- `sweep/cases/<wave>.ts` exports `cells: Case[]` (one entry per screen × variant, run by `sweep.spec.ts`), with the wave's own helpers above it.
- `sweep/cases/<wave>.flows.ts` exports `flows: Flow[]` (run by `interactions.spec.ts`). It is a second file because cells and flows keep separate helper sets (`ng`, `ngItem`, `settle` differ in shape).
- `sweep/waves/<wave>.json` is the wave's object (`title`, `screens`, `specs`, `rules`); the file name is its key, so `node sweep/wave.mjs <wave>` finds it.
- `sweep/loadCases.ts` reads the directory and takes any file name: `shipped` first, then `ux2-W<n>` by number (`ux2-W2` before `ux2-W10`), then every other file by name (a fix PR adds `cases/ux2-fix-<topic>.ts`, and `.flows.ts` beside it). The first case of a screen in that order also gets the `~light` cell at 1280, so keep one screen's first case in its wave's file; a fix file sorts last and only adds variants.
- A helper two waves share goes in `cellKit.ts` (cells) or `flowKit.ts` (flows); the pre-`/ng` screens are `cases/shipped.ts` and `shipped.flows.ts`.

## History and briefs

- `sweep/HISTORY.md`: the receipts of W0–W14. For each wave: its rules, the cells it changed, cleared or regressed, its commits, open questions and MRs. This file is the archive up to 2026-10-01: from then on each PR writes its own receipts to `sweep/history/<branch>.md` (the branch name with `/` as `-`), a file only that PR adds, so merges never conflict on it.
- `sweep/briefs/`: the one home for wave briefs (`W<n>_BRIEF.md`). It also holds `PUNCHLIST-v3.md`, the evidence list the waves closed, and `QUESTIONS.md`, where a wave writes a decision it cannot make. A new brief goes here in the same MR as its wave; the design handoff links here and does not copy it. Feature specs are not briefs: they follow CLAUDE.md and go to Kraft as work-item attachments. `sweep/WAVES.md` is the W0–W9 plan and the loop every wave follows.
- `design/handoff_v4/`: the rules the code follows, with sweep frames as the reference screens.

## Accepted flags

These stay flagged on purpose; do not "fix" them:

- `nested-scroll` = 2 on item pages: the inspector and the right pane are two independent scrollers (the model).
- `console` on `login/`: the 401 before sign-in, until the backend's `authenticated` field lands (Kraft-yx79s).
- `ellipsis` on the `data-allow-ellipsis` cells listed above. Only those eleven elements may carry the attribute.
