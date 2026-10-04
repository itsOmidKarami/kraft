# Kraft UI sweep

Screenshots + machine checks for every screen × data variant × viewport × shell state, against **mocked** `/api/**` so every display state (rate_limited, capped, budget, escalated, archived…) is reachable in milliseconds. No orchestrator, no fake agent.

CI does not run the sweep itself (its `contract/` folder is a separate suite, which CI's `ui contract` job does run). Run the sweep before opening a pull request that changes how a screen looks or lays out (see [Before an MR](#before-an-mr)); [`../README.md`](../README.md#four-layers-and-when-each-is-required) says when that is and when vitest or Playwright is what you need. Labels like `ux2-W<n>`, `R41` and "brief Decided 11" in this folder cite the maintainer's private UX V2 design notes, which a clone does not have (see CONTRIBUTING.md); the text beside each says what it means. The plain `W0`–`W14` waves are the earlier fix programme, and their briefs and receipts are tracked here (`briefs/`, `HISTORY.md`, `history/`).

## Install (once)

```bash
cd frontend && npx playwright install chromium
```

The harness is versioned here. `vite.config.ts` excludes `sweep/**` from vitest, so `npm test` does not pick up the Playwright specs.

## Run

```bash
cd frontend
npx playwright test -c sweep/playwright.sweep.config.ts        # ~1,500 shots, 4 workers, ~6–10 min
node sweep/collect.mjs                                           # → e2e-shots/sweep/manifest.json + FINDINGS.md
```

Subsets: `SWEEP_SCREEN=board,item-node SWEEP_WIDTHS=390,1280 SWEEP_VARIANT=long npx playwright test -c sweep/playwright.sweep.config.ts`

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

`clippedEllipsis` skips an element carrying `data-allow-ellipsis`: a deliberate one-line cut with the whole text in its `title`. Only the element carrying it (W10.D). Do not use this attribute anywhere else. It is allowed on exactly these, and the ones marked † have a `checks.spec.ts` case:

1. `.ng-crumb-repo` † and `.ng-crumb-current` † — the header's repo and item-title crumbs, the repo shrinking first (UX V2 W2, brief Decided 11)
2. `.item-one-line` † — the item page's question banner in a node view, the question on one line (UX V2 W5, Decisions §4)
3. `.lib-name` † — a component's name in the Library list, which keeps its first 14 characters and cuts a longer id such as `never-signal-processes-you-didnt-start` (UX V2 W12, R10)
4. `.rp-name-text` † and `.rp-cut` † — the Repos table's name and its steering and test cells (UX V2 W15)
5. `.an-name` and `.an-stop-label` — Analytics' node, repo and stop labels (UX V2 W16)
6. the review's file tree directories and file names, and the diff header's `.rv-file-path` (UX V2 W8)

The element must carry its full text in `title`. Anything else that ellipsizes still fails the check; a new use needs a decision first.

## The UI contract

`contract/` is the one part of this folder that asserts, and that CI runs. It drives the built SPA in a
browser on the same mocked `/api/**` as the sweep (`fixtures.ts`, `mockApi.ts`), with no Kraft server and
no Python:

```bash
just ui-contract                       # builds the SPA, serves it with `vite preview` on :4327, ~1.5 min
just ui-contract -g "sidebar"          # Playwright flags after it
CONTRACT_PORT=4400 just ui-contract    # another port, when 4327 is taken
```

- **Rows** (`shell.spec.ts`, `item.spec.ts`, `review.spec.ts`): one plain-language claim per test, named for
  the behaviour (`sidebar: unpinned, it takes no width (no icon rail) [decided]`). `[decided]` marks a
  behaviour that was chosen, so a change to it is a decision to revisit, not a fix. A row that fails is a
  regression, or a behaviour that changed on purpose: change the row in the same pull request and say why.
- **Icon-only audit** (`icons.spec.ts`, `screens.ts`): on every screen in `screens.ts`, each visible
  button, link or summary with no letter or digit in it needs an accessible name and a `data-tip`. A new
  screen or state that can hold an icon-only control belongs in `screens.ts`.
- **Seeds** (`fixtures.ts`, after `buildScenario`): what the base scenario lacks, added by mutating the
  built scenario (`app(page, url, { tweak: withReviewer })`): a gate's reviewer and producer, a fix round's
  outcome, a test result, a stop's cause and limit, beads, an escalation thread, light mode. Add one there
  when a row needs data the scenario does not have, not in the spec.

Prove a new row before trusting it: break the code it covers, confirm that row fails, restore the code.

## Axes

- Widths: 390, 768, 1024, 1100, 1280, 1440, 1920 (+1280×700).
- Data: `default`, `long` (140-char titles, 32-hex ids in text, 12 repos, 40-node timelines, 600-line logs, 40-file diffs), `many` (60 rows), `empty`.
- Shell (at 1280): sidebar open / rail, light mode, comfortable density, short viewport, group-by repo/template.
- Screens: one per page and pane of the UI (`board`, `board-peek`, `item`, `item-node`, `review`, `chains`, `library`, `harnesses`, `repos`, `policy`, `intake`, the settings pages, `analytics`, `search`, `login`, …) and the phone's (`phone-*`, at 390); `cases/*.ts` is the list.

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

## Waves

The UI's waves are the files `waves/ux2-W<n>.json`, so a wave runs as `node sweep/wave.mjs ux2-W<n>` and writes `e2e-shots/DIFF-ux2-W<n>.md`; the plain `W0`–`W13` files are the earlier fix programme. Until the cutover (ux2-W18) the new UI was served under a prefix and its screens' names began `ng-`, so the rule files up to `ux2-W17` and the `ux2-fix-*` ones match `^ng-` and no longer select anything: they are kept as history. `waves/ux2-W18.json` carries every one of their rules forward under the plain names.

W2's flows assert: a failed assertion is that step's error, which `flow-completes` reports.

## Adding a wave's cases, flows and rules

A wave **adds files and edits no shared list**; `sweep.spec.ts`, `interactions.spec.ts` and `wave.mjs` name no wave.

- `sweep/cases/<wave>.ts` exports `cells: Case[]` (one entry per screen × variant, run by `sweep.spec.ts`), with the wave's own helpers above it.
- `sweep/cases/<wave>.flows.ts` exports `flows: Flow[]` (run by `interactions.spec.ts`). It is a second file because cells and flows keep separate helper sets (`ng`, `ngItem`, `settle` differ in shape).
- `sweep/waves/<wave>.json` is the wave's object (`title`, `screens`, `specs`, `rules`); the file name is its key, so `node sweep/wave.mjs <wave>` finds it.
- `sweep/loadCases.ts` reads the directory and takes any file name: `ux2-W<n>` by number (`ux2-W2` before `ux2-W10`), then every other file by name (a fix PR adds `cases/ux2-fix-<topic>.ts`, and `.flows.ts` beside it). The first case of a screen in that order also gets the `~light` cell at 1280, so keep one screen's first case in its wave's file; a fix file sorts last and only adds variants.
- A helper two waves share goes in `cellKit.ts` (cells) or `flowKit.ts` (flows).

## History and briefs

- `sweep/HISTORY.md`: the receipts of W0–W14. For each wave: its rules, the cells it changed, cleared or regressed, its commits, open questions and MRs. This file is the archive up to 2026-10-01: from then on each PR writes its own receipts to `sweep/history/<branch>.md` (the branch name with `/` as `-`), a file only that PR adds, so merges never conflict on it.
- `sweep/briefs/`: the one home for wave briefs (`W<n>_BRIEF.md`). It also holds `PUNCHLIST-v3.md`, the evidence list the waves closed, and `QUESTIONS.md`, where a wave writes a decision it cannot make. A new brief goes here in the same MR as its wave; the design handoff links here and does not copy it. Feature specs are not briefs: they follow CLAUDE.md and go to Kraft as work-item attachments. `sweep/WAVES.md` is the W0–W9 plan and the loop every wave follows.
- `design/handoff_v4/` (and the earlier `handoff_v3`) is the maintainer's private design handoff, which the waves were built against. `design/` is gitignored, so a clone does not have it, and nothing here depends on your reading it. What the code enforces is in tests you can read (`ng/css.contract.test.ts`, `ng/css.collision.test.ts`, `ng/phone/contract.test.ts`, `ng/phone/css.test.ts`) and is summarised in [`../README.md`](../README.md); what this harness checks is the per-shot flags under [Output](#output) and the `Accepted flags` below. The sweep's own shots, taken on `main` (see [Baseline](#baseline)), are the reference screens.

## Accepted flags

These stay flagged on purpose; do not "fix" them:

- `nested-scroll` = 2 on the item page, the review page and the phone: a pane beside the canvas or list, each scrolling on its own (the model).
- `console` on `login/` and `phone-login/`: the 401 before sign-in. Likewise the cells whose state is an error the page reports (`board/offline`, `new-item/error`, `search/docs-error`, `chains/review-stale`, `policy/review-stale`, `item-cap/editor-refused`, `phone-board/offline`, `phone-area/policy-stale`), excluded in `waves/ux2-W18.json`.
- `ellipsis` on the `data-allow-ellipsis` elements listed above, and only those.
