> The receipts of the UI sweep programme (W0–W14): per wave, the rules that passed or failed, the cells that changed, commits, questions and MRs. Moved from `e2e-shots/RUN-SUMMARY.md`; the text below is unedited.
> `e2e-shots/sweep/` (the PNGs and manifests it cites) is not tracked; regenerate it with `node sweep/wave.mjs all --baseline`.

# Kraft UI fix run — summary

Scope, as changed mid-run: **W0 and W2 only**. Round-3 sweep additions and W1, W3–W9 were skipped.

Working branch `ui-waves` (from `main` @ `408a9de`). Each wave: `wave/<Wn>` → commit → fast-forward `ui-waves`.

| Wave | Branch | Commit | Acceptance |
|---|---|---|---|
| W0 · Item page layout | `wave/W0` | `aa896bf` | 6/6 ALL PASS |
| W2 · Tablet + breakpoint unification | `wave/W2` | `5d95aca` | 4/4 ALL PASS |

`ui-waves` = `408a9de` → `aa896bf` → `5d95aca`. Nothing pushed.

---

## Before the waves: the harness was photographing the wrong app

Three harness defects made the first round of numbers meaningless. All were fixed before a valid baseline was shot.

1. **Stale server.**
   - `playwright.sweep.config.ts` had `reuseExistingServer: true` on port 4173.
   - An orphaned `vite preview` from another worktree (`.claude/worktrees/bridge-cse_01Gmhdih6gE5PMS4Tibt5pva/frontend`, started 03:14) held that port.
   - So every shot, including my first baseline and first W0 shoot, was that worktree's build.
   - Now: port `SWEEP_PORT` (default 4317), `reuseExistingServer: false`, `--strictPort`, and `SWEEP_DIST` to serve a prebuilt dist.
   - The orphan server was left running; it isn't this run's.
2. **Two of three specs never ran.** `testMatch: /sweep\.spec\.ts/` skipped `elements.spec.ts` and `interactions.spec.ts`, which produced 0 tests.
3. **Rules that could pass blind.**
   - `elements.spec.ts` never wrote `element.collapsed`, and it skipped a 0×0 region (a failed screenshot meant `continue`). So "not collapsed, ≥320" could pass with no entries.
   - Flow steps waiting on a missing element hit the 60s test timeout and recorded no `step.error`.

**Valid baseline:**
- Shot from a clean scratch worktree of `408a9de` (`vite build`, served via `SWEEP_DIST`).
- 681 tests passed, 1118 shots.
- 82 of 94 `el-inspector` / `el-right-pane` cells were collapsed or under 320px.

## Harness edits (`frontend/sweep/**` — git-excluded via `.git/info/exclude`, so not in any commit)

- `playwright.sweep.config.ts`:
  - own port, no server reuse, `SWEEP_DIST`
  - `testMatch` covers all three specs
  - `actionTimeout: 8000`
- `checks.ts`:
  - `negativeDurations`: text nodes matching `/-\d+s\b/`.
  - `lowContrast`: WCAG ratio against the composited background.
    - Walks up to the first opaque ancestor background, alpha-blending translucent layers on the way.
    - Colours are normalised through canvas.
    - Ancestor opacity folds into text alpha.
    - Thresholds: 3.0 at ≥24px, otherwise 4.5.
    - Skips disabled controls; counts only viewport-visible text.
- `collect.mjs`: `neg-duration×N`, `contrast×N`, `focus-ring` flags.
- `interactions.spec.ts`:
  - per-frame `focusRingMissing`: the focused element matches `:focus-visible` and its outline, box-shadow, border and background equal an unfocused clone. A parent `:focus-within` ring counts as a ring. Clone, not `blur()`, because blur fires handlers.
  - per-frame negative durations
  - `inspector-tabs-selection@390` and `log-maximize@390` start on the phone node page (`#node=<current>`). The tabs and session rows live there (m05), not on the m04 stage list.
- `elements.spec.ts`:
  - records `collapsed` (a missing or 0×0 split region at ≥768 is recorded, not skipped)
  - dedupes the duplicate title `el/gate-tasks-long@1280`, which Playwright refused to load
- `sweep.spec.ts`:
  - a `~light` cell at 1280 for the first case of every screen
  - a `menu/header-more` case (W0 acceptance names it; the spec had no menu screen), opened via `.app-header [aria-haspopup=menu]`
  - `composer/overflow-menu` opens `.action-bar [aria-haspopup=menu]` (the old `button[aria-label*=more]` hit whichever "More" came first)
- `fixtures.ts`:
  - `settingsFor("empty")` is actually empty: hooks, templates, steering files, sessions.
  - `analyticsFor` totals gain `unplanned_touches_per_item` and `open_mr_to_green_ci_ms`. The backend always sends them (`src/kraft/analytics.py:288–289`, zero defaults), and without them Analytics threw `TypeError … reading 'toFixed'`.
- `mockApi.ts`: guards empty templates.

---

## W0 · Item page layout — `wave/W0` → `aa896bf` — ALL PASS

**Rules** (`DIFF-W0.md`):

| Rule | Result |
|---|---|
| not collapsed, height ≥ 320 on `el-inspector` / `el-right-pane` | PASS (was 82/94 failing) |
| neg-duration = 0 everywhere | PASS (see note) |
| flow steps complete: inspector-tabs-selection, log-maximize, graph-resize, item-tabs-keyboard | PASS |
| `item/gate-comfortable@1280~comfortable` differs from compact | PASS |
| no console errors on `item` / `el-` / `composer` | PASS |
| no newly flagged cells | PASS |

**Cells:** 672 changed, 148 cleared, 0 regressed, 511 still flagged.

Still flagged, by flag: ellipsis 413, contrast 417, offscreen 128, target<44 119, clipped-v 1. The top elements belong to later waves:
- W5.10: doc title/path
- W5.6: `control-hint`
- W5.1: app-header crumb hex id
- W5: tree names
- intentional: W0.7's left-ellipsized repo paths and W0.3's one-line judge note at fit steps

**Brief acceptance, checked in the PNGs:**
- `item/gate-long@1280` shows the gate card, Approve/Reject, stage graph and both panes without scrolling.
- `menu/header-more@1280` and `composer/overflow-menu@1280` each show an open menu with Archive / Open worktree / Copy id / Copy link.

**Note on neg-duration.** The count is 0 in the baseline too, only because the fixture clock (T0 08:00Z) is now in the past. The clamp shipped anyway, with unit tests (future start → `0s`, negative → `0s`).

**What changed:**
- Hero:
  - title clamps to 2 lines
  - description renders as markdown, clamped to 2 lines with more/less
  - task progress gets a full-width row
  - "+N submodules" chip opens Config → Repos, scrolling the inspector
- Split floor of 320px. A `ResizeObserver` steps `data-fit` tight → tighter → tightest; CSS reads it, and JS sets no pixel heights.
- Gate card:
  - content in a 720px column
  - findings and judge note side by side above 1280
  - "+N more" at fit steps
  - separate "waiting …" line
- One duration helper (`elapsedBetween` + `nodeRunSpan`) for hero, split header, stage-graph tooltip and phone stage list. Clamped ≥0, and frozen once the node's session exits.
- Completed/archived hero shows the last node ("completed 2h ago", "archived · read-only"). A never-started item reads "not started" with a "waiting to start" chip.
- Stage graph flag follows `pending_gate`.
- Tasks tab counts the selected node's sessions.
- The app header "…" (had no handler) and the action-bar "…" (now on every state) share one item menu.
- Title edit is a textarea growing to 3 lines.
- Comfortable density reaches the item page.
- The degraded-health note gets its own slot above the sidebar footer.
- Phone node-page log maximize works.
- Finished items select their last node.
- Log header:
  - wraps instead of scrolling its actions out of reach
  - session id shown as `first8…last5`
  - timestamps off 2.69:1 contrast

**Tests added:** `format.test.ts` (elapsed, elapsedBetween, nodeRunSpan frozen/running/future/negative/escalation, shortId), `index.test.tsx` (markdown description, Config → Repos + chip, Tasks count, not-started / completed hero, gate waiting, title textarea), `StageGraph.test.tsx` (gate flag), `ActionBar.test.tsx` (item menu on running and archived items), `Header.test.tsx` (header menu), `Phone.test.tsx` (phone maximize).

**Decisions:**
- **Progress row placement.** Hero progress goes full width under the title, per brief rule 1, though spec 38 draws it in the hero's right column. The brief is explicit and cites the squeeze evidence.
- **Not-started hero.** Follows spec 21.
- **Gate "shield" (rule 6).** An accent flag inside the gated pill, as spec 44 draws it. The old derivation keyed off `auto_escalate`, which the long fixture's node override turned off.
- **"Header …" (rule 9).** This is the app header's `More`, the one with no handler. The detail-meta "More" (Edit title/description) is kept.
- **Escalation turns.** They don't restart a node's run clock. Waiting time shows on the gate card, and in the action bar for capped/budget/question/escalated.
- **Fit steps beyond the brief.** Each step first closes page gaps and card padding. `tightest` also clamps the title to 1 line, hides the description and hides hero tags. Without that, the 320px floor could not hold at 1280×700.
- **Removed footer.** The Tasks footer "N tasks · newest first" was dropped; it counted sessions and called them tasks.
- **Log header compromise vs spec's one 41px row.** At ≤1280 it takes 2–3 lines (~50px of log). A scrolling chip row (W5.5's rule) would still leave chips past the viewport for the offscreen check.
- **`.log-t` colour.** Moved from neutral-700 to neutral-500.
- **Session id.** Shown as `first8…last5` per README §5. That's one piece of W5.1; W5's click-to-copy `<ShortId>` is still to build.

**Still wrong** (each filed as a bead):
- `item/escalated*@*` — the escalated card prints "no summary reported" above the message and carries dead space (W8.6). `Kraft-6ogs1`
- `item/not_started@1920` — "–" inspector head and an empty split, where spec 21 draws an intake card. `Kraft-pfqdb`
- `item/*@390` — breadcrumb overprint (repo + "Board" + hex id), repo name past the meta line (W3). `Kraft-0zqm5`
- `item/gate-long@1280` and every log view at ≤1280 — log header is 2–3 lines tall, not spec's one 41px row (see decision above; W5.5). `Kraft-mqcp6`
- Harness: the offscreen check measures unclipped rects, so content scrolled out of view inside an `overflow-x` container reads as offscreen. This blocks W4.1/W5.5's scrolling chip rows. The check needs a human call and was not edited. `Kraft-mv2nx`

---

## W2 · Tablet + breakpoint unification — `wave/W2` → `5d95aca` — ALL PASS

Pre-W2 baseline: the W2 screens re-shot at the W0 state (board/settings/analytics/new-item were last shot pre-W0) — 495 cells.

**Rules** (`DIFF-W2.md`, after three shoot/fix rounds):

| Rule | Result |
|---|---|
| overflow-x = 0 at 768 / 1024 | PASS |
| offscreen = 0 at 768 / 1024 | PASS (baseline: 14 cells) |
| no console errors at 768 / 1024 | PASS (was the Analytics fixture gap) |
| no newly flagged cells | PASS |

**Cells:** 218 changed, 13 cleared, 0 regressed, 364 still flagged.

- Still flagged, by flag: contrast 273, ellipsis 271, target<44 90, offscreen 29 (all at 390/1100+, outside W2's rule), input<16 28, setup 6.
- Cleared cells:
  - `analytics/empty@1280`
  - `item/escalated@1100`, `item/escalated-long@1100`
  - `item-changes/default@768`, `item-documents/default@768`, `item-timeline/default@768`
  - `settings-policy/default@1024` / `@1100` / `@1280` / `@1440` / `@1920`, `settings-policy/empty@1280`
  - `settings-repos/default@768`

**Frames checked by eye:** `item/running@768` (List/Detail), `settings-repos/default@768` (cards), `settings-policy/default@768` (full labels), `item/gate@1100` (wrapped action bar, hint shown, tabs on one line), `new-item/empty@768` (one column, on screen), `board/long@1024` (facets wrap, one-line chips), `board-peek/running-long@1024` (peek head wraps), `item-documents/default@1024`, `settings-chains/default@1100` (single column), `board/default@1280` (200/150 rows).

**Still wrong, seen in these frames** (later waves' rules, not W2's; each filed as a bead):
- `item-documents/*` — document list titles cut to one or two letters in the inspector (W5.10). `Kraft-xl77f`
- `settings-chains/default@1100` — `valid` status drawn as an input (W7.4). `Kraft-hzcqj`
- `board/default@1280` — the sort control sits on its own line under the facets (W4.7). `Kraft-cs5ip`

**Root cause of the tablet split.** It never stacked because `work_item.css` declares `.item-split`'s columns unconditionally and is bundled after `styles.css`. The stacking rule inside styles.css's media block always lost. That is also why the inspector was really 420px, not the intended 320px, at 1024–1279 before this wave.

**What changed:**
- Breakpoints: only `max-width` 767 / 1023 / 1279 plus `max-height` 719 remain.
  - `min-width` 768 and 641 became base rules, undone in the phone block.
  - The 1440/1439 rows folded into the base: board rows 200/150, board content capped at 1280 and centred.
  - Both 640 phone blocks moved to 767; analytics 900 → 1023; chains editor 1200 → 1279.
- `src/breakpoints.test.ts` greps every CSS file for other queries. Checked against the pre-W2 CSS, where it fails and names all eight old queries.
- Tablet item page (768–1023):
  - the inspector's tab strip sits over one pane showing the list or the selected row's detail
  - a List / Detail switch sits in the inspector head
  - picking a row goes to Detail; changing tab goes back to List
  - `usePhone.ts` gains `useMedia(query)`; `usePhone` stays at 767
- 1024–1279:
  - inspector 360px, its tabs on one line, session metrics lines wrap
  - action bar wraps: buttons + a two-line hint, then the secondary actions
- Under 1024:
  - the action-bar hint is hidden (spec 47)
  - settings pages go single column, label above field
  - the repos table becomes cards
  - dialogs go full width, New work item one column
  - wrap instead of running past the edge: board row meta, item meta line, doc pane head, timeline filters
- Board facet chips:
  - wrap on tablet and scroll only on phone
  - at ≤1279 the facet group wraps and a chip stays one line, capped at its row's width
- Peek (380px at ≤1279): the head wraps, the title stops being sticky, and the id shows as `first8…last5`.
- `work_item.css`'s `.budget-row` flex rule is scoped to the item Config pane. Unscoped, it overrode Settings → Policy's grid and cut labels to "Max active work…".

**Tests added:** `src/breakpoints.test.ts`; `index.test.tsx` (tablet List/Detail flow; no switch at desktop width).

**Decisions:**
- **Tablet item page (W2.2).** The rule's List/Detail toggle wins over spec 47's stacked picture. The rule is explicit.
- **One value at ≥1280.** Inspector 420px, which matches spec 38 at 1366. Board rows 200/150, per W4.2 and spec 45's laptop row.
- **Facet chips.** They wrap on tablet and scroll only on phone, per README §6 ("chip rows scroll horizontally on phone"). A scrolled tablet row left chips past the viewport, which the offscreen check can't tell from real overflow; the check was not edited (`Kraft-mv2nx`).
- **The 1024–1279 hint.** A two-line clamp instead of an ellipsis. Once shown there, the old ellipsis counted as a new flag.
- **The List/Detail switch's idle option.** Lifted to neutral-400, scoped to the inspector head; the shared segmented control read 4.23:1. Tokens are W1's.
- **Analytics console error.** It was a harness fixture gap, not an app bug (see the harness section).
- **Invisible-to-checks fix.** New Work Item ran off the left edge at 768; the offscreen check only looks right. Confirmed fixed in the PNG.

---

## Final sweep (all three specs, no `SWEEP_SCREEN`) — the new baseline

Run on `ui-waves` @ `5d95aca`, then `node sweep/collect.mjs`: **681 tests passed, 1118 shots, 743 flagged.**

| Flag | Pre-waves (`408a9de`, clean build) | Final |
|---|---|---|
| any flag | 905 cells | 743 cells |
| offscreen | 420 | 87 |
| clipped-v | 370 | 2 |
| ellipsis | 673 | 567 |
| contrast | 593 | 568 |
| target<44 | 164 | 167 |
| input<16 | 31 | 34 |
| console | 27 | 13 |
| setup | 40 | 21 |
| nested-scroll | 19 | 17 |
| chrome-moved | 1 | 0 |

- `el-inspector` / `el-right-pane` collapsed or under 320px: 82/94 before, **0/94** after.
- The pre-waves manifest is kept at `e2e-shots/baseline/pre-waves/manifest.json`.
- The small rises in `target<44` and `input<16` are phone cells (W3's rules). New controls became reachable at 390, and the phone node page now has a maximize button.

**Flow step errors left (4 of 164 frames), none under a W0/W2 rule:**
- `flow-gate-approve/01-read-document` @390 and @1280 — harness selector. The step asks for a *button* named "Read document", but the gate card renders it as an `<a class="btn">` link. Not fixed: W6's flow, and no waves after W2 were run.
- `flow-settings-save-roundtrip/04-chains-editor@390` — the phone Chains page has no clickable `default` template to open (W7.4, "phone shows the editor after picking a template").
- `flow-sidebar-toggle/01-collapse@1100` — no Collapse button, because 1100 opens on the rail. The punch list already accepts this.

**Focus rings — the check is not yet trustworthy.** `focus-ring` flagged 0 of 75 frames with a focused element, but 23 of those report no visible ring ("Select all", doc and tree rows). All 23 were mouse-focused; the check only judges `:focus-visible` focus, so it never ran on them. Filed as `Kraft-s400i`, to verify before W6 uses the rule.

**Stale shots moved, not deleted.** 54 PNGs in `e2e-shots/sweep/` came from earlier sweeps' cases that the current specs no longer produce (`doc-modal`, `log-modal`, `phone-node`, `toast`, `el-phone-list`, plus old `menu`, `item`, `board` and `settings-appearance` variants). They were moved to `e2e-shots/sweep-stale/`, so `e2e-shots/sweep/` holds exactly the 1118 cells in `manifest.json`. The originals are also in the root `kraft-sweep-*.zip` archives.

**Superseded manifests kept beside the new one:**
- `manifest.round2.jsonl` — the copied-in round-2 run
- `manifest.stale-server.jsonl` — the runs against the other worktree's server
- `manifest.pre-final.jsonl` — this run's wave shoots

## Beads filed this run

| Bead | What |
|---|---|
| `Kraft-6ogs1` | Escalated card says "no summary reported" above the message (W8.6) |
| `Kraft-pfqdb` | Not-started item page lacks spec 21's intake card |
| `Kraft-0zqm5` | Phone item breadcrumb overprints repo, Board and hex id (W3) |
| `Kraft-mqcp6` | Log header is 2–3 lines at ≤1280, not one 41px row (W5.5) |
| `Kraft-mv2nx` | Sweep offscreen check flags content clipped inside overflow-x scrollers |
| `Kraft-xl77f` | Documents tab cuts titles to one or two letters (W5.10) |
| `Kraft-hzcqj` | Chains draws `valid` status as an input (W7.4) |
| `Kraft-cs5ip` | Board sort control drops onto its own line (W4.7) |
| `Kraft-s400i` | Sweep focus-ring check has never been shown to fire |

## QUESTIONS.md

Not written. Every ambiguity got a decision, recorded in the wave sections above. The one call that needs a human, whether the offscreen check should respect scroll clipping, is `Kraft-mv2nx`: the harness rules forbid changing a check to pass a wave.

---

## Environment notes

- `frontend/sweep 2/` (an untracked copy of the harness) is collected by vitest. It shows as 3 failed files, which are Playwright specs, with 0 failing tests. It isn't this run's, so it wasn't deleted. Every vitest run here: all tests passed; the only failures are those 3 files.
- `.beads/interactions.jsonl`, the `design/` deletions and the untracked root zips were already modified on `main` before this run. They were left out of every commit.
- A scratch worktree of `408a9de` was created under the session scratchpad to build the clean baseline, then removed (`git worktree remove --force`, `git worktree prune`) once no later step needed it.

---

# Round 2 — W5, W4, W3

Scope, set after W0/W2: **W5, then W4, then W3**, on `ui-waves` from `5d95aca`. W1, W6–W9 and the round-3 additions were skipped. Two corrections came with it: fix the offscreen check first (Kraft-mv2nx), and take evidence from the new baseline (`e2e-shots/sweep/` at `5d95aca`), not the round-2 PNGs.

| Step | Branch | Commit | Acceptance |
|---|---|---|---|
| Harness · offscreen check | `ui-waves` | `ee00401` | new `checks.spec.ts`, 3 cases |
| W5 · Text overflow | `wave/W5` | `ffd7ec6` | 3/3 ALL PASS |
| W4 · Board, peek, archive | `wave/W4` | `85550e4` | 4/4 ALL PASS |
| W3 · Phone | `wave/W3` | `7749f86` | 5/5 ALL PASS |

`ui-waves` = `5d95aca` → `ee00401` → `ffd7ec6` → `85550e4` → `7749f86`. Nothing pushed.

Every baseline in this round was shot from a frozen build of the branch point (`vite build --outDir <scratchpad>`, `SWEEP_DIST`), so edits made while a run was shooting could not leak into its PNGs.

## Harness · offscreen check (Kraft-mv2nx) — `ee00401`

- `sweep/checks.ts`: an element past the right edge is not offscreen when its nearest horizontally clipping ancestor is an `overflow-x: auto/scroll` container that is itself inside the viewport. `overflow: hidden/clip`, or no clipping ancestor (the viewport cuts it), still counts.
- `sweep/checks.spec.ts`: a 390px chip row wider than the viewport — count 0 with `overflow-x: auto`; > 0 with `overflow: hidden` and with no container. The first case fails with the exemption removed (received 1).
- `sweep/` is git-excluded; `checks.ts`, `checks.spec.ts` and the config were force-added so the check's behaviour is on record. Later waves force-added `sweep.spec.ts` and `interactions.spec.ts` the same way when they changed.

**Refined during W5.** The correction said the element must intersect the scroller's rect. The log header's chip row stops short of the viewport edge (Follow / Copy / Maximize sit to its right), so a chip scrolled wholly past the row's own edge can still cross the viewport's without touching the row — and was counted. The overlap test is now vertical only (same row); the scroller must still be inside the viewport. Added a fourth case for exactly that layout; it fails under the literal condition (received 1). Recorded here because it is a reading of a decided rule, not a new decision: the correction's stated purpose was to unblock W5.5's scrolling log header.

## W5 · Text overflow — `wave/W5` → `ffd7ec6` — ALL PASS

**Rules** (`DIFF-W5.md`, fifth shoot):

| Rule | Baseline | Result |
|---|---|---|
| ellipsis = 0 at 1920 | FAIL (docs, config repos, tree names, settings identifiers) | PASS |
| clipped-v = 0 | PASS | PASS |
| no newly flagged cells | — | PASS |
| user: `item-documents/default@1280` shows full doc titles | cut to "Design the …" | full titles, 2-line clamp (PNG checked) |

**Cells:** 408 changed, 81 cleared, 0 regressed, 276 still flagged. Outside contrast (W1) and small targets (W3), what is left in W5's scope is ellipsis on phone rows at 390/768 and the maximized log strip at 1100/1280 (both fixed in the last W5 edits), and `new-item/no-repos@1280`'s setup timeout.

**What changed, in the requested order:**
- 5.10 Documents list: grid `24px minmax(0,1fr) auto auto`, title 2-line clamp, kind never wraps (Kraft-xl77f).
- 5.1 `<ShortId>` (`first8…last5`, full id in `title`, click copies) in the hero meta, peek head, log header, plugin last runs, archived rows; `shortIds()` for titles that embed an id (session docs, search results, escalation task rows, doc pane/modal titles). Item crumb is **Board › title**, 2-line clamp.
- 5.6 `control-hint` at every width, 2 lines, never an ellipsis; the <1024 hide and the 1279 override removed.
- Gate card judge note: 2 lines at fit steps.
- 5.5 Log header one row at every width: identity, meta and chips scroll in their own track; actions in an `auto` track (Kraft-mqcp6).
- 5.2 `.path`: whole, broken anywhere (see decisions). 5.3 list titles 2-line (docs, search results, board rows, task titles, task-row subs, maximized log title), modal/pane titles 3-line; search snippets drop markdown syntax. 5.4 identifier columns (hook, loop) `minmax(14ch, 2fr)`, value column yields; budget labels `minmax(120px, 360px)`; steering list `minmax(14ch, 280px)`; identifiers wrap. 5.7 config fields `160px minmax(0,560px)`. 5.8 timeline rows `minmax(0,1fr)` + nowrap time. 5.9 search list scrolls at 60vh inside the panel; result rows full width.
- W7.4 Chains: `valid` is a status badge, "1 node" singular (Kraft-hzcqj).
- W8.6 Escalated card quotes the turn's `escalation_message` when there is no summary; the borrowed doc-body padding that made its empty band is gone (Kraft-6ogs1).
- Hero meta wraps (a long repo name plus the id ran under the hero column at 1280).

**Tests:** `ShortId.test.tsx` (5.11: ShortId pattern, `shortIds`, `.path` in Documents, markdown strip); escalated card message; Header crumb and ActionBar hint pins updated to the new policy.

**Harness edits:** the checks.ts refinement above; `sweep.spec.ts` search cells typed into `input[type="search"], input`.last() — the last input on the board, not the palette's — so all 12 `search/overlay-*` cells were setup errors in the baseline and shot an empty palette. Now `input[aria-label="search"]`.

**Decisions:**
- **Crumb = Board › title, no repo.** The hero meta leads with the repo, and screens 14/41 start the crumb at the title. A long title clamps at 2 lines (header grows to fit) rather than ellipsizing.
- **W5.2 vs ellipsis@1920.** A left-ellipsized path is an ellipsis at 1920 in a 420px inspector. Paths are shown whole and break anywhere, as in screen 14.
- **W4.2 vs W5.3 and the @1920 rules.** Board row titles clamp at 2 lines instead of 1-line ellipsis.
- **W5.9 vs ellipsis@1920.** Bead hits clamp at 2 lines and the strip wraps; full text in `title`.
- **ShortId format.** Kept W0's `first8…last5` (README §5); WAVES' `c744…7b11` reads as an illustration.
- **Identifier columns as fractions, not `max-content`.** Rows of a list are separate grids; `max-content` would give each row its own tracks.
- **Budget label column** `minmax(120px, 360px)` so the inputs line up at one x, as in spec 29.

**Still wrong (not W5 rules):**
- Phone header at 390: W5's 2-line crumb crowds the 48px header (`flow-phone-node-page/01-tap-stage@390`) — replaced by W3.1.
- `new-item/no-repos@1280` — setup timeout (New work item is disabled with 0 repos); accepted in the punch list.
- Remaining chains work (YAML toggle, Add node) — `Kraft-b9syf`.

## W4 · Board, peek, archive — `wave/W4` → `85550e4` — ALL PASS

**Rules** (`DIFF-W4.md`, second shoot):

| Rule | Baseline | Result |
|---|---|---|
| offscreen = 0 on board / board-peek / archived | PASS | PASS |
| ellipsis = 0 on `board/long@1920` | PASS (W5's 2-line titles) | PASS |
| peek / selection-archive / sidebar-toggle flows complete | FAIL (`sidebar-toggle/01-collapse@1100`) | PASS |
| no newly flagged cells | — | PASS |

**Cells:** 117 changed, 1 cleared, 0 regressed, 72 still flagged.

**What changed:**
- 4.1 Facet bar holds one row at ≥768: Board measures which chips wrapped onto the hidden second line and lists them under a `+N` disclosure; the phone scrolls the row. The sticky bar is opaque with a hairline; group heads stick at `--facet-bar-h`.
- 4.7 Sort stays at the end of that row (Kraft-cs5ip). Archived uses the board's sort disclosure ("Sort · archived date"), a 320px search on the same row, one "nothing here yet" line when empty; analytics by-node / by-repo tables with no rows show one line, no header.
- 4.3 Peek `min(520px, 45vw)` at every desktop width; ✕ 12px from the viewport edge. 4.4 Header: id · state · Open → · ✕, then repo · template, no wrapping. 4.5 The log block takes the remaining height.
- 4.6 The keyboard ring sits on the row, inside its box; `fix·1` / retry pills keep their own width.
- 4.8 The open Settings group persists across a reload, read in the initial state like the rail choice.
- 4.9 Archive (floating bar and per row) toasts "N items archived · Undo" for 6s; Undo restores. `Toast` gains an optional action and duration.
- 4.10 Group by template already headed groups by template id; pinned with a test.

**Tests:** Board (Undo toast restores; group by template), Toast (action + duration), AppNav (Settings group persists), Archived (empty line, no native select), Analytics (empty tables are one line).

**Harness edits (`interactions.spec.ts`):**
- `sidebar-toggle/01-collapse`: under 1280 the sidebar starts as the rail (accepted in the punch list), so there is no Collapse to press; the step presses it only when present.
- `board-selection-archive/04-archived-chip`: clicked the first text matching `/archived/` — after 4.1 the toast, or the Archived chip folded under `+N`. It now clicks the visible Archived link, opening `+N` first when needed.

**Decision:** W4.1 says chips `max-width: 160px` with ellipsis, `+N` after 8. The `board/long@1920` ellipsis rule forbids cutting a repo name, so overflow is by width into `+N` and a chip always shows its whole label.

**Frames checked by eye:** `board/long@1280` (one row, `+13`), `board-peek/running-long@1100` (495px peek, scrim, two-row head), `board-peek/gate@1920`, `flow-board-selection-archive/03-archive@1280` (toast with Undo), `flow-sidebar-toggle/04-reload-persists@1280`, `archived/default@1280`, `flow-peek-open-close/03-esc-closes@1280` (row ring, pill width).

**Still wrong:**
- `archived/default@1280` crumb reads "Archived 1 items" — `Kraft-h7igq`.
- `analytics/empty@1280` is not a W4 screen, so it was not re-shot in the wave; the unit test covers the empty tables and the final sweep shoots it.
- `board-peek/gate@1920`: the facet row runs under the peek (covered, not clipped; the chip count is measured without the peek).

## W3 · Phone — `wave/W3` → `7749f86` — ALL PASS

**Rules** (`DIFF-W3.md`, fifth shoot):

| Rule | Baseline | Result |
|---|---|---|
| offscreen = 0 at 390 | PASS | PASS |
| overflow-x = 0 at 390 | PASS | PASS |
| target<44 = 0 at 390 | FAIL (58 frames, up to 46 per frame) | PASS (0 frames) |
| peek / phone-node-page flows complete at 390 | PASS | PASS |
| no newly flagged cells | — | PASS |

**Cells:** 197 changed, 5 cleared, 0 regressed, 324 still flagged (almost all contrast, W1's). The user allowed ≤2 small targets per frame; the rule was driven to 0 instead, so nothing is accepted against it.

**What changed, in the requested order:**
- 3.1 Phone item header (Kraft-0zqm5): the app header steps aside on an item page at ≤767 and `PhoneTopBar` is the only header — ‹ Board and the title on one sticky 44px line; no repo, no id, no "N of M · swipe" (the swipe gesture still works).
- 3.2 Hero meta on phone: repo · template · id · status (bead id, submodules and attachment tags are desktop-only).
- 3.3 44px floor at ≤767 on buttons, chips, tabs, menu items, sort menu rows, hook rows, selects, the settings back link, the peek's Open → and MR links, the rate-limit link and the tab bar. In-text links grow their hit box with inline padding (the line box does not move). A switch is a 44px box that draws its 34×20 track; the Done checkbox is a 44px box that draws a 16px square. Inputs 16px; chip rows scroll-snap.
- 3.4 `--bottom-nav-h` is defined once (`BottomNav.css`); `main` pads by it.
- 3.5 Composer sheets: one action pair — Cancel and the title in the header, the primary pinned top-right; the body's own Cancel and its duplicate heading go. The subtitle is the item title, not the id.
- 3.6 Phone peek: a 60vh bottom sheet with a handle that takes it full height; the scrim dims but lets a long-press reach another row, which swaps the sheet.
- 3.7 Node page: the back label is the item title. There is no tab-swipe hint or long-press-repo copy to remove.
- 3.8 Phone intake: the sheet scrolls, Create stays pinned, and the overrides panel opens from an "Advanced · overrides" disclosure.
- 3.9 The tab bar hides while a composer, intake, peek or repo sheet is open.
- 3.10 Settings on phone: Repos was already cards (W2); Plugins and Appearance rows fit at 390 with the 44px floor.
- Phone board: group heads stick below the taller (44px-chip) filter row.

**Tests:** Phone (header is back + title; node back label is the title), Header (hidden on a phone item page).

**Harness edit (`sweep.spec.ts`):** `peek()` opened the peek with a click. On phone a tap navigates (m03) and a long-press opens the peek, so every `board-peek/*@390` cell was a setup error — in the baseline too — and had never shot a peek. It now long-presses under 768, as `flow-peek-open-close` already did. That exposed the peek's Open → and MR links as small targets, fixed in the fifth shoot.

**Decisions:**
- **3.1 header scope.** WAVES says `PhoneTopBar` is ‹ Board + title; the user's note says back + title only. The ⋯ item menu stays on the action bar (W0.9) rather than the header, and the "N of M · swipe" context goes.
- **3.5 action pair.** The primary moves into the header by CSS (pinned top-right of the sheet) instead of restructuring every composer's props; the body keeps only fields.
- **3.3 hit boxes without growing visuals.** Inline padding for in-text links, a 44px box drawing a smaller track or square for switches and checkboxes. Visual size of the controls is unchanged.

**Frames checked by eye:** `item/gate@390`, `item/gate-long@390`, `composer/escalate@390`, `flow-peek-open-close/01-click-row@390`, `board-peek/gate@390`, `flow-phone-node-page/01-tap-stage@390`, `flow-new-item-full/07-scroll-bottom@390`, `board/default@390`, `board/many-scrolled@390`, `settings-appearance/default@390`, `settings-plugins/default@390`, `flow-gate-reject/03-type@390`.

**Still wrong:**
- `flow-new-item-full/06-budget@390` — the flow never opens "Advanced · overrides", so it types the budget into the hidden panel. Harness step. `Kraft-ow8wo`
- `flow-phone-node-page/02-swipe-tab@390` — a horizontal drag selects log text; there is no tab swipe. No hint promises one, so 3.7's "works or the hint goes" holds.
- `item/not_started@1920` — spec 21's intake card is still missing. Left open: a new card with start node, first gate, budget and slots, not a 30-minute change. `Kraft-pfqdb`
- Phone board filter row (`board/default@390`): "Sort · recently updated" sits beside a chip row that scrolls under it; readable, but tight.


## Final sweep, round 2 (all specs, no `SWEEP_SCREEN`) — the new baseline

Run on `ui-waves` @ `7749f86` from a frozen build, then `node sweep/collect.mjs`: **685 tests passed** (681 + the four `checks.spec.ts` cases), **1118 shots, 613 flagged**. The wave shoots' manifest was set aside first as `e2e-shots/sweep/manifest.round2-waves.jsonl`, so `manifest.jsonl` holds only this run. Every PNG under `e2e-shots/sweep/` is in `manifest.json`; none were stale.

| Flag | Pre-waves (`408a9de`) | After W0/W2 (`5d95aca`) | After W5/W4/W3 (`7749f86`) |
|---|---|---|---|
| any flag | 905 cells | 743 | **613** |
| offscreen | 420 | 87 | **1** |
| clipped-v | 370 | 2 | 2 |
| ellipsis | 673 | 567 | **186** |
| contrast | 593 | 568 | 548 |
| target<44 | 164 | 167 | **2** |
| input<16 | 31 | 34 | 29 |
| console | 27 | 13 | 13 |
| setup | 40 | 21 | 5 |
| nested-scroll | 19 | 17 | 23 |

- `el-inspector` / `el-right-pane` collapsed or under 320px: still **0/94**.
- **offscreen 1** — `analytics/long@390`: the repo `<select>` sizes to its longest option (a 60-character repo name). Analytics filters are not a W3/W4/W5 screen.
- **target<44 2** — `search/page@390`, `search/doc-viewer@390`: the phone Search page's rows. Not a W3 screen. `Kraft-pzf7i`
- **setup 5:**
  - `flow-gate-approve/01-read-document` @390 and @1280 — harness selector (asks for a button; the gate card renders a link), as in round 1.
  - `flow-settings-save-roundtrip/04-chains-editor@390` — phone Chains page has no clickable template to open (W7.4), as in round 1.
  - `menu/header-more@390` — new: W3.1 removed the app header from phone item pages, so its ⋯ is not there to click; the item menu is on the action bar. `Kraft-92daa`
  - `new-item/no-repos@1280` — accepted in the punch list.
- **nested-scroll 23 (was 17)** — the four `board-peek/*@390` cells now shoot a real peek sheet (they were setup errors before), and long composer / description / chain YAML textareas scroll inside a scrolling page. The punch list already accepts nested scrollers on item pages.
- `sidebar-toggle/01-collapse@1100` is no longer a flow error (W4 harness fix).
- **contrast 548** is almost all W1's (light-mode tokens and the check's known false positives on disabled and decorative text), untouched by this round as instructed.

## Beads, round 2

| Bead | Status | What |
|---|---|---|
| `Kraft-mv2nx` | closed with `ee00401` | Offscreen check vs overflow-x scrollers |
| `Kraft-xl77f` | closed with `ffd7ec6` | Documents tab cut titles to one or two letters (W5.10) |
| `Kraft-mqcp6` | closed with `ffd7ec6` | Log header 2–3 lines instead of one row (W5.5) |
| `Kraft-hzcqj` | closed with `ffd7ec6` | Chains drew `valid` as an input (W7.4) |
| `Kraft-6ogs1` | closed with `ffd7ec6` | Escalated card "no summary reported" above the message (W8.6) |
| `Kraft-cs5ip` | closed with `85550e4` | Board sort control on its own line (W4.7) |
| `Kraft-0zqm5` | closed with `7749f86` | Phone item breadcrumb overprint (W3.1) |
| `Kraft-pfqdb` | **open** | Not-started item page lacks spec 21's intake card — more than a 30-minute change |
| `Kraft-b9syf` | filed | Chains YAML toggle and Add node do nothing (rest of W7.4) |
| `Kraft-h7igq` | filed | Archived crumb says "Archived 1 items" |
| `Kraft-ow8wo` | filed | Sweep flow types the phone budget into the hidden overrides panel |
| `Kraft-pzf7i` | filed | Phone /search rows are under 44px |
| `Kraft-92daa` | filed | Sweep case `menu/header-more@390` targets a header menu the phone no longer has |
| `Kraft-s400i` | open (round 1) | Focus-ring check not yet shown to fire |

## QUESTIONS.md, round 2

Not written. Every conflict between a technique in WAVES.md and an acceptance rule was resolved toward the rule and the spec PNG, and is listed under that wave's decisions.


---

# Round 3 — W1, W6, W7/8

Continued from `ui-waves @ 7749f86`. Every shoot came from a frozen build (`vite build` to a scratch dist, `SWEEP_DIST=`). Nothing pushed.

| Step | Commit | Sweep |
|---|---|---|
| Harness: contrast calibration | `9cb3614` | checks.spec: pass/fail pair + decoration cases |
| W1 · Light mode | `54b8135` | 3/4 rules · 73 changed, 29 cleared, 0 regressed — FAIL: login 401 |
| W6 · Composers, toasts, focus, post-action | `33587f3` | 3/3 rules · 87 changed, 0 cleared, 0 regressed — ALL PASS |
| W7/8 · Settings + correctness slice | `22fb0b9` | 5/6 rules · 320 changed, 34 cleared, 0 regressed — FAIL: login 401 |

## Harness: contrast check calibration (9cb3614)

`checks.ts` lowContrast now also skips text inside `[aria-hidden=true]`, placeholder-styled
elements (`.placeholder`, `[data-placeholder]`), and text composited below 0.5 opacity that has
no role and sits outside any button/link. Muted/secondary text is still judged.
`checks.spec.ts`: #9397ab on #0f1019 → 0, #5a5d70 → 1; decorative cases → 0, a faded button → 1
(that case returns 3 on the old check). Committed alone before W1's baseline.

## W1 · Light mode

Rules (sweep/waves.json W1, plus the new `no-flag-increase` kind):
- PASS contrast = 0 on /~light/ (baseline: 16 of 17 cells, 973 failures)
- FAIL no console on /~light/ — login/default@1280~light, one 401 (QUESTIONS.md, Kraft-yx79s)
- PASS contrast count does not rise on dark cells
- PASS no newly flagged cells

Changes:
- palettes.css: every palette's light block restates the whole set — bg, surface, surface-2,
  text, text-muted, text-faint, border, accent, accent-2, five status fills + ink, and all
  27 ramp steps. The ramps are mirrored: each step keeps against the light ground the
  contrast it has against the dark ground; text steps 100–600 floored at 5.2:1. Nocturne gets
  the same blocks as the other four (plus a dark role block). Generator: scratchpad
  genlight.mjs (method recorded in the palettes.css header).
- Light mode block: accent no longer `var(--color-accent-700)` (that is a pale tint once
  ramps mirror); shadows ink from --color-text; --color-section → accent-900 (login glow).
- styles.css: dark defaults for surface-2/text-faint/border and status tokens; status fills
  on .tag-accent (needs you), .tag-neutral (done/paused), sidebar badge, running chain fill,
  capped/budget glyph borders. Opacity-faded text → colour: .kbd, .chip-count,
  .chain-pill-count, .plan-task-n, locked config fields.
- theme.ts: applyTheme persists {palette, mode} to localStorage `kraft.theme`; savedTheme().
- main.tsx: applies the saved theme before anything awaits; /api/theme is the one session
  probe — on 401 it mounts straight into Login (App initiallyLocked) with no bootstrap, no
  socket. Login console errors 10 → 1.

Tests: palettes.contrast.test.ts (25: text/muted ≥4.5, faint ≥3 on bg+surface, both modes,
every palette; light ramp text steps ≥4.5; status fills ≥3 as fill and ≥4.5 as text, ink ≥4.5;
light block restates the role set), theme.test.ts savedTheme ×2, App.test.tsx initiallyLocked.

Harness edits:
- wave.mjs: rule kind `no-flag-increase` (count per cell must not rise vs baseline).
- waves.json W1: `{ no-flag-increase, contrast, ^(?!.*~light) }` (added, nothing weakened).
- sweep.spec.ts: `board/default@1280~light-firstpaint` — after one normal visit, reload with
  /api/theme held 1.5s and shoot at DOMContentLoaded. Baseline build: a flat dark frame
  (the flash). W1 build: flat light frame.
- sweep.spec.ts: locked cases with a mode shell seed `kraft.theme` (the only theme channel a
  locked page has).

By eye: board/default, item/gate-light, board-peek/gate, item-log/maximized,
settings-policy/default, analytics/default, login/default — all @1280~light.

Decisions:
- Light ramps mirror instead of one hand-tuned light value per use site (W1.1 "redefine the
  whole set together"); contrast-matched mirroring keeps every existing ramp-step choice in
  styles.css meaningful in both modes.
- Status fills get tokens; running/done chain fills and glyph text stay on the ramp.

Still wrong:
- login/*: one 401 console error (needs backend, Kraft-yx79s).
- Contrast check reads background-color only, not gradients (the login glow passed while
  unreadable) — noted, not changed (Kraft-aqrs9).

## W6 · Composers, toasts, focus, post-action

Harness first (before W6's baseline, described in the W6 commit):
- Focus-ring check (Kraft-s400i) moved to `checks.ts focusRingMissing(page, anyFocus)`. On
  keyboard-driven steps (flows item-tabs-keyboard, search-keyboard; gate-reject 02) it judges
  document.activeElement however it got focus; elsewhere only :focus-visible. checks.spec:
  Tab-focused outline:none button → 1; ringed → 0; mouse-focused ringless → 0, and 1 with
  anyFocus (the old check returns 0 there). Baseline and after: every keyboard step records
  `false` with a ring visible — the zero is measured, not skipped.
- flow-gate-approve/01-read-document: link OR button named /read document/i (it is <a class="btn">).
- mockApi.ts mutates on exactly four POSTs: approve (gate cleared, active), reject (active,
  current node = reject_to), pause/resume, resume with steer (active + a pending session).
- flow-pause-steer-resume/02-steer-open steers the item it just paused (it used to goto a
  different paused item — the "steers another item" in the punch list was the harness).

App:
- W6.1 toast stack top-right under the header (56px, 8px in), phone above the bottom nav, ≤3.
- W6.2 composer textarea padding-top/scroll-padding-top 8px, `field-sizing: content` 3→10
  lines; inside the item page capped by what the viewport has left (see decisions).
- W6.3 useActionBar acts on the id it was given, `run` resolves true/false and holds a
  `pending` phase until the store re-reads the item; ActionBar composers close on success,
  GateCard reject closes on success; "pending…" shown in the gate card / action bar mid.
  Intake toasts "Created · paused" / "Created · started" after navigating to the created id.
- W6.4 Composer: ⌘/Ctrl-Enter submits, Escape cancels; Cancel/Escape restore `main`'s scroll
  (W6.10) and focus the trigger (found again by text when it re-mounted).
- W6.5 SearchOverlay: option ids, `aria-activedescendant` on the input, active row scrolled
  into its own list, focus restored on close; Enter acts on the active row.
- W6.6 one `:focus-visible` ring (2px accent-500, offset 2px) over interactive elements;
  template chips (`.chip[role=radio]`) show their selection like a pressed filter chip.
- W6.8 Tabs: roving tabindex, ArrowLeft/Right/Home/End move focus, Enter/Space select.
- W6.9 useModal focuses `[data-autofocus]` first; intake title carries it.

Tests: ui Tabs roving; Toast ≤3; ActionBar ⌘-Enter submits + closes, Escape restores focus;
SearchOverlay ArrowDown/Enter on active row; useModal data-autofocus.

Decisions:
- Composer growth cap on the item page: `max(3 lines, min(10 lines, 100vh − 710px))`. Ten lines
  at 800px pushed `.detail` past the viewport (clipped-v regression on steer/steer-retry
  filled-long@1280) because the page never scrolls and the split keeps 320px; a flat 20vh
  still overflowed. Grows to the full 10 lines from ~940px tall.
- Template cards keep role=radio + aria-checked (a single-choice group), not aria-pressed;
  the selected look is what W6.6 asked for.
- W6.7 needed no code: keyboard focus already scrolls the nearest container (flow
  item-tabs-keyboard/03 shows the focused control in view).

Still wrong:
- composer/answer-filled-long@1100,@1280: the long quoted question pushes `.detail` past the
  viewport (clipped-v, present in the baseline too) — Kraft-migvn.
- flow-new-item-full/06-budget@390 types into the hidden overrides panel (harness, Kraft-ow8wo).
- flow-new-item-full/07-scroll-bottom@1280: the intake's sticky footer settles above the
  viewport bottom and body content shows beneath it (baseline too) — Kraft-fafow.

## W7/8 · Settings + correctness (one branch, one commit)

Harness: `waves.json` gains `W78` — W7's screens and rules, W8's screens and its
`no-console ^login/` rule, plus board and archived (the phone header and the crumb). Nothing
weakened. `fixtures.ts analyticsFor`: weekly_merged keyed by Monday like the server (the old
keys were T0 minus whole weeks, and `long ? 52 : 12 - i` put every long week at 52).

App:
- Board phone header: New work item is an icon-only "+" (aria-label kept) at 390; the
  "all repos" pill sits on the facet row instead of its own band.
- Hero description: the 2-line clamp shows prose (plainMarkdown strips headings and list
  markers); "more" renders the full markdown.
- Chains (Kraft-b9syf): `valid` badge in the page head beside "N nodes · M gates" (problems
  keep their box in the list); a YAML button (aria-pressed) switches the editor pane between
  the node form and YAML/diff, two columns instead of three; ⊕ buttons are named "Add node …"
  — the harness's "add node" click found nothing before — and adding switches back to the
  form with the new node selected.
- Archived crumb (Kraft-h7igq): "Archived · 1 item" / "Archived · N items".
- Not-started item (Kraft-pfqdb): on the default tab the split is the intake card
  (template, repo, budget, will start at, first gate, Start) beside the chain it will walk;
  Edit chain / any other tab opens the normal split.
- W8.1 the question in full above Answer; W8.2 `docBody` drops a title-repeating H1 and
  headings with no body (doc pane + modal); W8.3 timeline groups "created" / node /
  "completed" / "abandoned", never "—"; W8.4 "nothing completed in this range" only when
  totals.completed is 0; W8.5 result count over every result row, lag note its own line;
  W8.7 done in W1 (single probe); W8.8 verified — hero says "not started", tag "waiting to
  start", no paused chip.
- W7.1 Plugins "kind" labelled (Segmented aria-labelledby); W7.3 segmented controls and
  selects in settings capped at 420px; W7.5 severity toggles are chips, budget label column
  `minmax(120px, max-content)`; W7.6 SaveRow is a sticky save bar with "unsaved changes";
  W7.8 8px between switch and sentence, footer a complete sentence; W7.9 `/settings` is an
  index page at every width, each section with a line. Settings list names/paths/commands
  wrap to 2 lines (no ellipsis at 1920); phone chain template ids in their own span (the
  save-roundtrip flow's `^default$` click timed out at 390).

Tests: TemplatesPage YAML toggle + Add node selects; NotStarted (fields, Start, chain);
format docBody ×2; updated Header crumb, settings index (desktop + repos), item page
not-started + description preview.

Decisions:
- W8.1 "full text" vs the item page that never scrolls: the question renders in full but in a
  box capped at 4 lines that scrolls inside (run 1 regressed question-tasks-long@1280 to
  clipped-v when it pushed `.detail` past the viewport).
- Hero preview drops heading *lines* before plainMarkdown: stripping only `##` left
  "Context The verify node…" run together (item/gate-long@390, run 1).
- Not-started: the card replaces the split only on the default tab, so "Edit chain" (Config)
  still reaches the node config; the bar keeps its own Start (two Start buttons, same action).
- Chains editor is two panes; the YAML/diff tabs live inside the YAML view.
- Peek state chip prints words ("not started"), not the state identifier (W8.8).

By eye (run 1/2): flow-settings-save-roundtrip/05-yaml-toggle@1280, 06-add-node@1280,
board/long@390, item/not_started@1920, settings-index/default@1280, item/gate-long@390.

Still wrong:
- login/*: one 401 console error — same as W1 (QUESTIONS.md, Kraft-yx79s); W78's
  `no-console ^login/` stays failing.

## Final sweep, round 3

`ui-waves @ 22fb0b9`, frozen build, all specs once, then `node sweep/collect.mjs`: **690 passed**,
1119 shots, **520 flagged** (round 2 final: 1118 shots, 613 flagged).

| Flag (cells) | Round 2 final | Round 3 final |
|---|---|---|
| contrast | 548 | 486 (all dark cells; `~light` 0 of 28) |
| ellipsis | 186 | 148 |
| input<16 | 29 | 29 |
| nested-scroll | 23 | 17 |
| console | 13 | 13 |
| setup | 5 | 2 |
| offscreen | 1 | 1 |
| target<44 | 2 | 2 |
| clipped-v | 2 | 2 |

- Collapsed item splits (W0's 320px floor on el-inspector / el-right-pane): 1 of 94 —
  `el-inspector/not_started-tasks-long@1280`, which has no inspector any more (the not-started
  card replaces it on the default tab); it is also the one stale PNG. Obsolete sweep case,
  Kraft-3xqc9.
- Setup failures: `menu/header-more@390` (obsolete case, Kraft-92daa) and
  `new-item/no-repos@1280` (accepted in the punch list). Round 2's gate-approve read-document
  and chains-editor@390 failures are gone.
- Console: `login/*` 7 cells, one 401 each (Kraft-yx79s); `flow-search-keyboard/04–06` 6 cells,
  a 404 because the search fixture links results to a work item that does not exist
  (Kraft-hrlqs).
- Dark-mode contrast was out of W1's scope (its rule was only that no count rises); the
  remaining dark flags are mostly `.tree-name.mono`, `.field-hint`, `dt/dd`, links and the
  bottom-nav badge.

## Beads, round 3

- Closed: Kraft-s400i (focus-ring check fires, W6), Kraft-b9syf (Chains YAML toggle + Add
  node), Kraft-h7igq (Archived crumb), Kraft-pfqdb (not-started intake card) — the last three
  with W7/8.
- Filed: Kraft-yx79s (public auth-state field so login never logs a 401), Kraft-aqrs9
  (contrast check ignores gradients), Kraft-migvn (answer composer's long question overflows
  the item page), Kraft-fafow (intake sticky footer leaves body showing beneath it),
  Kraft-3xqc9 (obsolete not-started el-inspector case), Kraft-hrlqs (search fixture 404).
- Still open from earlier rounds: Kraft-ow8wo, Kraft-pzf7i, Kraft-92daa.

## QUESTIONS.md, round 3

Written, one question: W1's `no console on ~light` and W78's `no-console ^login/` cannot reach 0
from the frontend — a locked instance has no public auth signal, and Chromium logs the one 401
probe. Recommendation: a backend field (Kraft-yx79s). Both rules were left failing, not
weakened.

## W10 · closing wave

`wave/W10` (13 commits on `ui-waves @ 1b257d5`), baseline from a frozen build of 1b257d5 with the
W10 harness, acceptance and final sweep from a frozen build of HEAD `9eb03be`. Final: **8/8 rules
pass**, 612 + 59 + 27 tests passed, 1134 shots, **186 flagged**, 0 regressions vs the baseline.

Acceptance (`sweep/waves.json` W10, screens and specs = all): contrast = 0 · clipped-v = 0 ·
target<44 = 0 at 390 · offscreen = 0 · no console outside `login/` · every flow completes ·
setup = 0 · no newly flagged cells — all PASS.

Harness (each its own commit, sweep/ force-added):
- W10 rules; `wave.mjs` reads `"all"` for screens/specs.
- Kraft-aqrs9: contrast reads gradient grounds (every colour stop, worst decides; a <4px layer is a
  divider, not a ground; an unparseable image fails as `gradient`). checks.spec case.
- W10.D: the overflow-x scroll-container exemption no longer applies to a panel (fixed, dialog,
  the peek, or a scroller >80% of the viewport wide and over half its height — height keeps a
  phone chip strip exempt); `data-allow-ellipsis` skips the element carrying it. checks.spec cases.
  Fixtures: two docs attached at intake in every variant at `.claude/worktrees/<id>/docs/superpowers/plans/…`
  (90+ chars). Widths 960 and 1000 for board-peek and the item page.
- Kraft-ow8wo (phone budget step opens the overrides), Kraft-92daa (header-more desktop-only;
  composer/overflow-menu@390 already shot), Kraft-3xqc9 (no el-inspector for not_started;
  el-fullpage/not_started-tasks-long@1280 stays), Kraft-hrlqs (search results link to their
  item), new-item/no-repos removed. `sweep-stale/` and the superseded `manifest.*.jsonl` deleted.

App:
- A (dark contrast, 486 → 0): measured grounds first. `--color-text-muted` is the 500 step in
  dark for every palette (clears bg, surface and the selected-row fill accent-900); neutral-600
  text → muted; Changes tree file rows had no colour (UA black, 1.19:1); accent text on selected
  rows, in-text links and Nocturne's `.btn-primary/.btn-ghost/.tag-outline` → accent-400;
  `.search-row-sub` → neutral-400; bottom-nav badge ink = page ground on the accent fill.
  palettes.contrast.test.ts pins each in every palette. Nothing needed aria-hidden.
- B: Kraft-migvn (question caps at 4 lines, 2 under 1280; footnote shares the actions' line on
  the item page), Kraft-fafow (dialog is its own scroller, footer on its bottom edge),
  Kraft-pzf7i (phone search 44px), Kraft-yx79s frontend half (uses `/health.authenticated`
  when sent; the backend does not send it, bead open).
- D: peek head grid `minmax(0,1fr) auto auto`, `overflow-x: hidden`, ShortId pinned by a unit
  test; Documents rows stacked (title + chips / one-line left-cut path / chips drop to line 3
  when they no longer fit), paperclip-only attached chip at an inspector of 520px or less.
- Also: analytics phone repo filter held to the viewport (the round-1 offscreen cell).

By eye (final run): board/default@1280, item/gate-long@1280, item-changes/long@1280 (tree names
readable), settings-policy/default@1280, composer/answer-filled-long@1100 and @1280,
flow-new-item-full/07-scroll-bottom@1280 (nothing beneath the footer), search/page@390,
item-documents/default and long@1280, el-inspector/gate-documents-long@1280,
board-peek/gate@1024, running-long@960, escalated-long@1100 (short id, Open → and ✕ visible),
analytics/long@390.

Still wrong / not in scope:
- login/*: one 401 each (7 cells) until the backend sends `authenticated` (Kraft-yx79s).
- Phone /search "lagging index" hint has no gutter (present in the baseline): Kraft-bj9pq.
- The Documents chip breakpoints (520 / 600px) are width guesses for the two known chips.

| Flag (cells) | Round 3 final | W10 final |
|---|---|---|
| shots | 1119 | 1134 |
| flagged | 520 | 186 |
| contrast | 486 | 0 |
| ellipsis | 148 | 156 |
| input<16 | 29 | 29 |
| nested-scroll | 17 | 18 |
| console | 13 | 7 (login/ only) |
| setup | 2 | 0 |
| offscreen | 1 | 0 |
| target<44 | 2 | 0 |
| clipped-v | 2 | 0 |

Collapsed splits: 0 of 93 (round 3: 1 of 94, the removed not_started inspector case). Ellipsis and
nested-scroll counts rose with the 15 new cells (960/1000 widths); neither is a W10 rule.

## W11 · UX wave (item card, board rows, chains, analytics, inspector scope, document titles)

`wave/W11` (11 commits on `ui-waves @ 9eb03be`), one commit per section in the brief's order
A B C I J F G H D E, plus one A follow-up found by eye during F. Baseline from a frozen build of
the W10 head with the W11 entry in `sweep/waves.json`; every section re-shot on its own frozen
build; final sweep from a frozen build of HEAD `c1e7d1ad`. Final: **6/6 rules pass**, 0 regressions,
678 unit tests passed, 1156 shots, **190 flagged**. `ui-waves` fast-forwarded to `c1e7d1ad`
(not pushed). The one e2e spec W11 edited, `e2e/phone.visual.spec.ts` (the chains pill tap,
m15 KPIs one per row, Review changes as a More actions item), ran against `serve.py` on a build of
`c1e7d1ad`: 13 passed, 1 skipped. The skip is "the document viewer hides what a phone cannot do".
Its own conditional `test.skip` fires when `.doc-row` counts 0, and that line predates W11. No
`phone-05-document.png` exists from any run, so the test has never gone past it. The phone node
page passes Documents its node, nodes and scope. `count()` does not wait for the list to load, so
the check is racy (Kraft-lo5bi).

Commits: 7f1e526e A · 740a9882 B · 298415c2 C · b456ced5 I · 461e897a J · fd313639 F ·
4906501e A follow-up · c56d3bc1 G · b71f07f5 H · 1776469c D · c1e7d1ad E.

Acceptance (`sweep/waves.json` W11; screens item, item-*, el-*, composer, board, board-peek,
settings-chains, analytics, flow-gate-*, flow-pause-steer-resume, flow-peek-open-close,
flow-board-selection-archive): offscreen = 0 · clipped-v = 0 · target<44 = 0 at 390 · no console
outside `login/` · every flow completes · no newly flagged cells — all PASS after each section.

QUESTIONS.md, W11: the one-line ellipsis A.1/B.2 mandate vs the checks.ts ellipsis rule. Decided
(option 1): `data-allow-ellipsis` on exactly `.detail-meta-part`, `.board-row-title`,
`.board-row-meta`, the peek header id/meta line and the W10 Documents path, each with the full text
in `title` and a checks.spec case; sweep/README.md lists the five as the allowlist.

Harness (described in each section's commit):
- `wave.mjs`: a trailing `*` in a wave screen is a prefix match (before, "el-*" matched nothing).
- elements.spec REGIONS: el-gate-card → `.item-card`, el-action-bar → `.item-card-actions`;
  checks.ts chromeRects action-bar selector likewise (selector only).
- sweep.spec: clickBtn falls back to the card's More actions menuitem; composer/overflow-menu uses
  `.item-card`; board-peek/escalating added (390, 1280); search/page and search/doc-viewer type the
  query (/search never read `?q=`, so both cells were empty pages in every earlier round,
  Kraft-7ra9t); settings-chains editor/editor-scrolled and the chains-editor flow step click the
  verify pill (no templates column).
- interactions.spec: flow-board-approve (1280), not in W11's screens, looked at by eye.
- fixtures.ts (long): two heading-less session summaries whose body has no `#` line.
- checks.spec: cases for `.detail-meta-part`, `.board-row-title`, `.board-row-meta`.
- sweep/README.md: the `data-allow-ellipsis` allowlist; entry 1 (`.doc-path`) unused since G.

App:
- A: `ItemCard` replaces ActionBar, GateCard and EscalatedCard on the item page — per-state title,
  body and one button row, composers inline, More actions = the item ⋯ OverflowMenu (icons,
  disabled rows with hints, dividers, arrow keys). Header: meta line of parts (repo · template ·
  bead · id · submodules · from spec) and a run line (chip · node · n/m · elapsed · fix cycle ·
  capped). The action bar component, its CSS and tests are deleted; el-action-bar captures the
  card's button row. Follow-up: the meta line is a one-line line-clamp, not per-part shrink.
- B: board rows one line — glyph · title · meta (repo · age · reason) · chain · one state-sized
  button (Approve and Resume act in place with a spinner; Steer & retry, Raise budget, Answer and
  Reply open the peek on that composer). Row reasons per state.
- C: phone rows 64px, repo · age · reason, no buttons.
- I: the peek renders the same ItemCard (`variant="peek"`); Gate, BudgetCard, CappedCard,
  PausedCard, Escalate, SkipControl and LogModal deleted with their tests.
- J: escalating items derive through `useItemStates()` (bootstrap hydrates needs_human items):
  Running group sorted first, "escalation running · elapsed", excluded from every needs-you count.
- F: Timeline shares Tasks' this node · all scope (lifted into Inspector); this node lists the
  node's events flat with a folded "N earlier nodes · M events · span" row; the right pane marks
  the selected event.
- G: Documents follow the scope — gate document pinned, this node's documents, folds per other
  node, a filter; rows are icon · title · node · hook · age (+ session id) · kind.
- H: `docTitle()` — a title that is only an id becomes the document's opening line, else
  `Session · <hook>`; used by Documents rows, the Doc pane, DocumentModal and search results.
  Backend half: Kraft-z1c62.
- D: Chains editor is one page — head row with template ▾ (every template, New…, Duplicate,
  Delete disabled pending Kraft-lwtco), YAML, Revert, Save; the pill strip; one card with the node
  form and that node's editable YAML fragment. The phone uses the same page (m13 drill-down gone).
- E: Analytics leads with three tiles (Completed, Lead time, Cost); unplanned touches and MR →
  green CI move to the stop-reasons footer; tiles stack at 26px on a phone.

As-built decisions (not covered by the brief):
- A: the gate document link keeps its per-gate label ("Review spec/plan/chain", else "Read
  document"); human_review_approval has no document link (Review changes is in More). More keeps
  "Cancel work item" as a trailing danger row with a confirm step (the only abandon path). No
  action sheet exists, so the phone keeps the popover. abandoned keeps Archive. escalated's
  "Apply as steer & retry" is More's first row. The reject composer stays inline on a phone.
  Non-gate card titles reuse the as-built hint copy.
- B/C: only Approve and Resume act from the row; Steer & retry, Raise budget, Answer and Reply
  need words, so they open the peek on that composer. human_review keeps "Review to approve".
  paused rows read "paused at <node>". Done rows lose Archive and ⋯ (checkbox + floating bar
  archives). The "from spec" chip left the board meta line. The meta line is a one-line
  line-clamp (text-overflow pushed the reason offscreen).
- I: the peek card hides stats and the document link and keeps the judge note; Review changes /
  Edit chain / Open item close the peek and open the item page. LogModal had one entry point
  (CappedCard) and went with it. OverflowMenu's Escape listener is a capture listener so Escape
  closes the menu, not the peek. The peek header was not changed, so its allowlist slot is unused.
  The I.5 density sentence went into design/handoff_v3/README.md, which is untracked.
- J: the list endpoint has no escalation state, so bootstrap hydrates every needs_human item and
  views derive through `useItemStates()`; a row can show the underlying stop until its hydrate
  lands.
- F: one scope for Tasks, Timeline and Documents, lifted into Inspector; the fold row wraps to two
  lines (not on the allowlist); it says "earlier nodes" or "other nodes"; a "see Timeline" link to
  another node's group opens under "all" (Kraft-a4js). The phone keeps its own scope.
- G: the tab count comes from the list (shown once the tab has been opened); documents with an
  unnamed or no node sit in a trailing "other" fold; with no node selected everything shows; the
  auto-pick prefers this node's document; fold and sub-line wrap, no ellipsis.
- H: "only an id" also covers the session's own id and "Session <id>" (the indexer's real form,
  which the brief's regex misses). List rows carry no body, so id-titled summaries read
  `Session · <hook>`; search results derive from the snippet.
- D: New… and Duplicate prompt for a name and refuse a taken one; the legend keeps "drag to
  reorder" (Kraft-nqqld); at 390 Save wraps to a second head row; on_failure tags lost the UA
  button fill (the contrast flag) and chains inputs restate 16px on a phone. styles.order.test
  now pins that templates.css no longer uses `.template-editor`.
- E: fix cycles print as the API's mean per verify (Kraft-kt204); "$X each" drops when nothing
  completed; the controls stay the static 8-week chip plus two selects (no range selector
  exists); the footer shows even over an empty table; three tiles stay in a row down to 768 and
  stack only at ≤767; the per-gate rejected breakdown is dropped.

By eye: item cards (gate, question, done, capped, escalating); board@1280/1100/390 and long;
flow-board-approve; board-peek gate/capped/escalated@1280 and @390, escalating; item-timeline
default/long/capped and the 1100 fold; item-documents default/long/running-long/long-scrolled;
search/page@1280; settings-chains default@1280, editor@1280, editor@390, long@1920;
flow-settings-save-roundtrip 04/05/06@1280, 04@390; analytics default@1280, long@1920,
default@390, empty@1280, default@1280~light.

Still wrong / not in scope:
- Analytics Cost reads "fix cycles per verify": the API sends a mean, not the brief's count
  (Kraft-kt204); the fixture's 88 renders as "88.0 per verify".
- Chains legend says "drag to reorder"; reorder is ← → buttons (Kraft-nqqld).
- /search ignores `?q=` (Kraft-7ra9t). search/doc-viewer lands on the item page (Kraft-hrlqs), so
  no cell shows DocumentModal's title.
- Template Delete has no API route (Kraft-lwtco); summary prompts don't emit a `#` title yet
  (Kraft-z1c62), so id-titled summaries in the list read `Session · <hook>`.
- The peek loses CappedCard's per-cycle trace and log entry (deleted with the old peek cards).
- Analytics long fixture shows negative "done" counts for the last two repos (fixture data,
  present before W11).

- board-peek/running-long@390 is a `setup` cell: the harness long-presses a row that sits under
  the fixed BottomNav, so the peek never opens (Kraft-k08aw). It was already flagged in the W11
  baseline, so it is not a regression.
- login/*: the 401s are unchanged (Kraft-yx79s).

| Flag (cells) | W10 final | W11 final |
|---|---|---|
| shots | 1134 | 1156 |
| flagged | 186 | 190 |
| contrast | 0 | 0 |
| ellipsis | 156 | 161 |
| input<16 | 29 | 28 |
| nested-scroll | 18 | 17 |
| console | 7 (login/ only) | 7 (login/ only) |
| setup | 0 | 1 (Kraft-k08aw) |
| offscreen | 0 | 0 |
| target<44 | 0 | 0 |
| clipped-v | 0 | 0 |

The 22 new cells come from board-peek/escalating, flow-board-approve and the extra flow steps.
Ellipsis is not a W11 rule and still counts only elements off the allowlist.

Beads: Kraft-z1c62 (summary prompts emit a `#` title, H's backend half), Kraft-lwtco (DELETE
/templates/{tid}), Kraft-kt204 (fix-cycle count), Kraft-nqqld (chains legend copy), Kraft-7ra9t
(/search `?q=`), Kraft-k08aw (sweep phone long-press under the BottomNav), Kraft-lo5bi (e2e phone
document-viewer test always skips), Kraft-1e51a (local-only planning.spec board-load timeout).

MR: W0–W8 and W10 landed on `main` as squash merges, so `wave/W11` does not merge cleanly (a
trial merge conflicts in 35 files). Its commits were cherry-picked onto `origin/main` as
`ui-waves-w11`, and that `frontend/` tree is identical to `wave/W11`. Checks on that branch:
- `npm ci`, `npm run build`, `npm test`: 682 passed.
- First full e2e run: 32 passed, 5 failed, 1 skipped.
  - `chain`, `lifecycle` and `planning` used selectors from before W11. Commit `4ec02f59`
    (`a109127a` on the MR branch) fixes the selectors and keeps every assertion.
  - Steering and Appearance failed only inside the full local run and pass alone. `planning` times
    out loading the board locally on `origin/main` too (Kraft-1e51a).

**!221** (`ui-waves-w11` → `main`, `release::patch`): pipeline 2847217549 passes (`frontend`,
`release-impact`, `frontend-e2e` 37 passed / 1 skipped). GitLab reports it mergeable with no
conflicts. Not merged. Local `ui-waves` is at `4ec02f59`.

## W12 · closing fixes (crumb, document pane head, chains inputs, analytics columns, overflow chip)

`wave/W12` (5 commits on `ui-waves @ 4ec02f59`), one commit per item. W11's acceptance entry is reused
as the brief says (same screens and rules, nothing new): baseline `node sweep/wave.mjs W11 --baseline`
from a frozen build of 4ec02f59, acceptance and final sweep from a frozen build of HEAD `cf89d0c1`.
Final: **6/6 rules pass**, 0 regressions, 683 unit tests passed, 1156 shots,
**180 flagged**.

Commits: 592527d5 W12.1 · f2223f08 W12.2 · d85b4a75 W12.3 · 007b95c7 W12.4 · cf89d0c1 W12.5.

App:
- 1 · The item title crumb is one line, cut with an ellipsis, full title in `title` (was a two-line
  clamp that made the header taller).
- 2 · The document pane head is three lines: title (one line, `title`), the path cut from the left so
  the filename survives (full path in `title`), then kind · written · indexed / not indexed yet.
- 3 · The chains node form's text inputs (id, delay) cap at 320px.
- 4 · Analytics: by node's name column is `minmax(14ch, max-content)`, so `pre_mr_rebase` reads whole
  and the share bar gives; by repo's repo column follows the same rule, and its right-aligned items
  column takes the slack. One grid per table with subgrid rows; the phone cards go back to flex.
- 5 · The facet overflow chip reads "+N more ▾" with `aria-haspopup`, same chip.

Harness:
- `data-allow-ellipsis` allowlist (sweep/README.md) grows from five to seven for the two cuts the brief
  mandates: 6 `.app-header-crumb-current`, 7 `.doc-modal-name`; slot 1 `.doc-path` now names the
  pane's path line. A checks.spec case each (17 checks.spec cases pass).
- fixtures.ts: the document body's "Status: draft · repo … · path …" lead line removed.
- interactions.spec `archived-chip` clicks `.board-more > summary` (the "more filters" label is gone).

As-built decisions (the brief's premise did not match the code):
- 3 · There is no Select component; Appearance's dropdown is a native `<select className="input">` and
  Policy has none. The chains selects already use that markup, so they stay; the test pins it.
- 2 · The "Status: draft" line was never rendered by the app — it was the sweep fixture's document
  body (no backend prompt writes one), so it went from the fixture.
- 2 · No path left-cut utility remained (W11.G removed `.doc-path` with the Documents row path); the
  pane's path line re-creates it with W10.D's markup, reusing allowlist slot 1.
- 2 · Only the pane's title is one line; DocumentModal shares `.doc-modal-name` and keeps its
  three-line clamp. Kind and "written …" stay on the note line.
- 5 · A phone never collapses facet chips (Board measures overflow only above 767px; every chip sits
  in the scrolling row), so there is no overflow chip to put in that row.
- tsc once reported TS2742 in `settings/testing.tsx`: a stale `node_modules/.tmp` build cache from the
  MR worktree's symlinked `node_modules`. Clearing the caches made `tsc -b` clean; no source change.

By eye (frozen build): item/gate-long@1280 (crumb one line), item-documents/long@1280 (title, left-cut
path, note; no status line in the body), settings-chains/editor@1280 (id 320px), analytics/default@1280,
long@1920 and default@390 (pre_mr_rebase whole; repo numbers on the right edge; phone cards intact),
board/default@1280 ("+3 more ▾") and board/long@1280 ("+13 more ▾").

Acceptance (W11 entry): offscreen = 0 · clipped-v = 0 · target<44 = 0 at 390 · no console outside
`login/` · every flow completes · no newly flagged cells — ALL PASS on the second run (195 changed, 10
cleared). The first run failed "no console" on one cell, `item-config/not-started@390`. That cell took
30.8s to load, the full sweep right after it was clean, and the re-run on the same build passed — the same
one-off load timeout W11 saw on item-changes/default@1920.

Still wrong / not in scope: board-peek/running-long@390 is still the one `setup` cell (Kraft-k08aw),
and login/* keeps its 401s (Kraft-yx79s).

| Flag (cells) | W11 final | W12 final |
|---|---|---|
| shots | 1156 | 1156 |
| flagged | 190 | 180 |
| contrast | 0 | 0 |
| ellipsis | 161 | 151 |
| input<16 | 28 | 28 |
| nested-scroll | 17 | 17 |
| console | 7 (login/ only) | 7 (login/ only) |
| setup | 1 (Kraft-k08aw) | 1 (Kraft-k08aw) |
| offscreen | 0 | 0 |
| target<44 | 0 | 0 |
| clipped-v | 0 | 0 |

The 10 cleared cells are all Analytics cells: analytics/default at 768, 1024, 1100, 1280, 1280~light,
1440 and 1920, and analytics/long at 1100, 1280 and 1920. Each had one ellipsis flag, the `.node-name` cut
that W12.4's max-content column removes. The crumb and document-pane cuts never counted as text-overflow
flags before (a two-line wrap and a line clamp), so they clear nothing; the allowlist keeps them from
adding flags.

## W13 · Documents rows, Timeline rounds

`wave/W13` (6 commits on `ui-waves @ cf89d0c1`), one commit per section A–E. Section A includes a small backend change, allowed this wave. The W13 entry was added to `sweep/waves.json` before the baseline (`node sweep/wave.mjs W13 --baseline`, frozen build of cf89d0c1, 160 cells). Acceptance and the final sweep ran on a frozen build of the tip `1201f381`.

Final: **5/5 rules pass** · 0 regressions · 703 unit tests passed · 1156 shots · **180 flagged**.

Commits: fa31288d A · 057fcbc8 B · 53e9f480 C · 37e9c556 D · 1201f381 E.

Acceptance (W13 entry, screens item-documents, item-timeline, el-inspector, el-right-pane, flow-inspector-tabs-selection, search):
- offscreen = 0
- clipped-v = 0
- no console outside `login/`
- flow-inspector-tabs-selection completes
- no newly flagged cells

All PASS (61 changed, 0 cleared). The five cells still flagged were flagged in the baseline too: `el-right-pane/gate-tasks-*@390` and `item-documents/*@390`, one ellipsis each.

App:
- A · `GET /work-items/{id}/documents` adds `attempt`, `round` and `session_status`, taken from the row's worker session: one keyed lookup, because worker_sessions is in the state db and documents are in the index. Artifacts and attachments get null. Tests: tests/test_api_retry_open_log.py and tests/test_artifact_api.py (`just test --no-testmon`, 2 passed).
- B · Documents rows:
  - A summary row is `hook · run label · time`, then the cleaned title on one line.
  - Run label: `turn N` for an escalation, `round N` once a fix loop is involved, else `attempt N`; just the hook when the server sends no run info.
  - `cleanTitle()` drops a bead id and the item's title (whole words).
  - Artifact rows keep their title first, with `hook · time`. No row shows its node or session id.
  - Newest run first. The filter matches hook, run label, title and path.
  - The pane header has an eyebrow with the session's ShortId.
- C · Timeline list:
  - `roundsOf()` / `nodeRounds()` build rounds from `node_started`, `fix_cycle_started`, `judge_verdict` and `findings_measured`.
  - "this node": `ROUNDS · N`, newest first, the current round open, 44px round headers over 40px session rows and a findings line. Escalation turns and node-level events sit at their time.
  - "all": one folded row per node.
  - Rows are buttons with arrow keys and Left/Right fold. Selection values are `session:`, `round:` and `event:` under the existing `tnode` key.
- D · Right pane stream:
  - A session is one row; a task run is one row.
  - Findings list severity · left-cut `file:line` · message. The judge row quotes its reasoning. Gate artifacts sit on their own line.
  - `waiting Nm` rows mark gaps over 2 min. `HH:MM:SS` on a minute's first row, then `+Ns`. 2px rail.
  - With nothing selected, the pane streams the node.
- E · Tests:
  - rounds on the capped fixture item (4 rounds, boundaries, verdicts)
  - escalation turns kept apart
  - stream collapsing
  - gap threshold
  - verdictWord

  The capped fixture gains a fix session per cycle and "continue" verdicts on cycles 1–2. Every `stop…` verdict reads `stop`.

Harness:
- waves.json W13.
- fixtures.ts: documentsFor emits the run fields; the capped item has real fix rounds.
- tsconfig.test.json includes sweep/fixtures.ts.

As-built decisions (the brief left these open, or its premise did not match the code):
- waves.json: `no-new-flags` is not a rule kind in wave.mjs; "no newly flagged cells" always runs. So the entry lists the four explicit rules and says so in its title, instead of carrying a kind wave.mjs would silently ignore. The first write reflowed the whole file through json.dump; that was reverted and the entry re-added in the file's own compact style.
- A: the join is a keyed lookup across the two databases, not SQL. Fixture artifacts now have no session id, since they never had a session.
- B:
  - ShortPath does not exist. The pane path and the stream's `file:line` reuse W12.2's `.doc-path` left-cut markup, which is allowlist slot 1.
  - A summary's second line is a line-clamp. The row's `title` is the path, so that line is not an allowlisted text-overflow cut.
  - An id-only title has no second line.
  - Artifact rows keep `docTitle()` uncleaned.
  - The pane's run label comes from the store's sessions: DocumentDetail carries no attempt or round. In sweep cells the fixture's document session ids are not the item's session ids, so the eyebrow shows no run label there. Kraft-vrztp.
  - The note line keeps only indexed / not indexed yet. The gate artifact's eyebrow reads "artifact".
- C:
  - The URL key stays `tnode`; there is no new `selection` param. W11's `node:<seq>` and a bare node still parse.
  - Nothing is auto-selected, because D.4 streams the node. A bare link to another node still switches to all (Kraft-a4js).
  - `node_started` opens round 0 and is also a node-level row.
  - A task_progress run is one row in the list too.
  - Other node-level events stay rows (`type · detail`).
  - The Inspector tab count still counts events; the list header counts rounds.
  - A single round has no header.
- D:
  - The all | gates | tasks chips stay.
  - The stream runs oldest first.
  - Session times come from the events first, then the store. Offsets use `elapsed()` (`+1m`).
  - A round's stream is its sessions' events plus the session-less events in its interval. A session's stream is its events plus the node's task_progress while it ran.
  - Rows keep `data-type` for the e2e specs.
  - D.5 phone: the phone item page has no Timeline tab (W3), so there is no list to push from. The ≤1023 List/Detail toggle already opens the stream. Kraft-b9tu5.
- E:
  - The judge's real verdicts are continue / stop_needs_human / stop_downgrade, so every `stop…` reads `stop`.
  - Slip: the first write of timelineHelpers.test.ts replaced the existing tests. They were restored from HEAD before commit, so the committed diff adds only (two import lines changed).
- tsc: the E test's import of sweep/fixtures.ts failed TS6307 (not in the test project) until tsconfig.test.json listed it.

By eye (frozen builds, per section):
- item-documents: default@1280, long@1280, running-long@390 (the phone item page)
- el-inspector: running-documents-long@1280, gate-timeline-long@1280
- el-right-pane: running-documents-long@1280, gate-timeline-long@1280 (the artifact path overflowed at first; it now sits on its own left-cut line)
- item-timeline: default@1280, capped@1280 before and after the fixture rounds, long@1920, long@390 (the phone page)
- flow-inspector-tabs-selection: 02@1280 and 02@390; 0 step errors

Still wrong or out of scope:
- board-peek/running-long@390 is the one `setup` cell (Kraft-k08aw).
- login/* keeps its 401s (Kraft-yx79s).
- The sweep pane eyebrow has no run label (Kraft-vrztp).
- Phones have no Timeline tab (Kraft-b9tu5).

| Flag (cells) | W12 final | W13 final |
|---|---|---|
| shots | 1156 | 1156 |
| flagged | 180 | 180 |
| contrast | 0 | 0 |
| ellipsis | 151 | 151 |
| input<16 | 28 | 28 |
| nested-scroll | 17 | 17 |
| console | 7 (login/ only) | 7 (login/ only) |
| setup | 1 (Kraft-k08aw) | 1 (Kraft-k08aw) |
| offscreen | 0 | 0 |
| target<44 | 0 | 0 |
| clipped-v | 0 | 0 |

Flag counts are unchanged: the W13 cells changed in 61 places, and none of them gained or cleared a flag.

Beads: Kraft-vrztp (fixture document session ids), Kraft-b9tu5 (phone Timeline).

MR: `main` already had W11 (!221, squash-merged; its `frontend/` matched the W11 tip). W12 and W13 were cherry-picked onto `origin/main` as `ui-waves-w12-w13`, and **!223** was opened against `main` with `release::patch`.

Checks on that branch:
- `npm ci`, build and `npm test`: 707 passed.
- ruff: clean.
- `just test --no-testmon` on the documents endpoint and its callers: 68 passed.

The full local e2e run had 3 failures and 1 skip:
- `chain.spec` asserted the pane's old "sessions" kind text, which W13.B moved into the eyebrow. The follow-up commit on `wave/W13` makes it assert the eyebrow's `hook · run` instead; it was cherry-picked to the MR branch.
- The Steering and Appearance settings tests failed only inside the full local run, as in W11; both passed in CI then.

CI on !223:
- **Pipeline 1:** `frontend-e2e` failed only `chain.spec` (36 passed), and the fix above was pushed.
- **Pipeline 2 (`b2c9f2b9`):** lint-and-test, slow-tests, frontend and release-impact all passed. `frontend-e2e` failed one test, `regression.spec` "appearance density and board prefs persist after reload" (Expected checked, Received unchecked). W12/W13 don't touch that page, and pipeline 1 passed it on the same app code. The test reloads straight after clicking Save, which can abort the PUT: Kraft-bws10. I retried the job; the user had asked for the MR to merge once mergeable, so auto-merge was already set (squash, delete source branch, pipeline must pass).
- **Retry (job 16487491183):** 37 passed, 1 skipped. Pipeline 2847468751 went green and !223 auto-merged at 2026-09-14T13:49Z: squash `98e63157`, merge `49182a88`, source branch deleted. `main`'s `frontend/` now matches `wave/W13` (`12fb0d65`).

## W14 — closing items (`wave/W14`)

Acceptance on the final frozen build (`df0085e2`):
- `node sweep/wave.mjs W13`: **5/5 rules pass**, 45 changed, 1 cleared, 0 regressions.
- `node sweep/wave.mjs W11`: **6/6 rules pass**, 197 changed, 3 cleared, 0 regressions.

Full sweep and collect: 721 passed, **1158 shots, 162 flagged** (W13: 1156 / 180). The 2 extra shots are the new board cells.

| flag | W13 | W14 |
|---|---|---|
| flagged | 180 | 162 |
| contrast | 0 | 0 |
| ellipsis | 151 | 152 |
| input<16 | 28 | **0** |
| nested-scroll | 17 | 17 |
| console | 7 (login/ only) | 7 (login/ only) |
| setup | 1 (Kraft-k08aw) | 1 (Kraft-k08aw) |
| offscreen / target<44 / clipped-v | 0 | 0 |

The one extra ellipsis cell is the new `board/repo-sheet@390`, which carries the same two facet chips as `board/default@390`. Unit tests: 711 passed, then `styles.order.test` 7/7 after the follow-up commit.

Commits:
- `8628a7d1` **A** — Timeline left pane shows rounds only.
- `587fdb1b` **B** — verified, no change; empty commit carrying the note.
- `30cc5817` **C** — harness fixes.
- `6101b1e4` **D** — Documents · 0 explained.
- `df0085e2` **C.3 follow-up** — phone inputs at 16px.

### A · Timeline left pane is rounds only
- **Left pane:** one 44px row per round, `round N · HH:MM → HH:MM · Xm`, with `S sessions · F findings · judge: <verdict>` (or `running`) on the right. No fold, no session rows.
- **Node-level events:** those inside a round are not rows. A `node_started` or `node_completed` within 60s of a round's edge is dropped. The round end is exclusive, so a gate at the exact edge stays.
- **Escalation turns:** stay one row each.
- **Right pane:** session rows are selectable. Click or Up/Down selects `session:<id>`, Enter opens the log. A session in a round streams that round with its row lit, and the left pane lights that round.
- **Seen in the cells:** `item-timeline/capped@1280` cut `round 2 · 10:22 → 10:25 · …`. The title column is now max-content and the counts wrap on the right.
- **As built / noted:**
  - "all" keeps its node folds; the brief's "no fold" is about rounds.
  - In the `long@1920` fixture the review round runs 13:42 → 13:48, and `gate_requested` at 13:47:48 falls inside it. By the brief's rule the gate is not a left-pane row; the right pane shows it.

### B · Chains editor inputs
- **W12.3 is in:** `d85b4a75` capped text inputs at 320px.
- **Frame is current:** `settings-chains/editor@1280` was shot at 16:10, after that commit.
- **Selects unchanged:** `gate_after`, `fix_loop` and `reject_to` stay native `select.input`. The brief names "the app Select (as Appearance/Policy)", but no Select component exists: Appearance's select is the same native `select.input`, and Policy has none. Adding one would be a new primitive, so it's kept as built: **Kraft-059md**.

### C · Harness
1. `analyticsFor` by_repo `done` is `Math.max(0, 9 - i)`; `mrs` had the same `9 - i` and got the same floor.
2. New cells `board/selection-bar@1280` (two Done rows ticked → "2 selected · Archive") and `board/repo-sheet@390`.
   - As built, a row long-press opens the peek, and the repo sheet opens from the phone's repo pill, so the cell taps the pill.
   - RepoSheet has no dialog role, so the cell waits on `.repo-sheet`: **Kraft-4pfl5**.
3. `input<16` now passes at ≥ 15.5px, pinned by a `checks.spec` case.
   - The calibration cleared nothing: the flagged phone fields were 13–14px. The phone `input, select, textarea { font-size: 16px }` rule lost to class sizes (`.input` 14px, `.cap-row .input` 13px).
   - Per the brief, the inputs were fixed, not the check: the rule is now `!important` (follow-up `df0085e2`), and input<16 is 28 → 0.
   - Looked at `new-item/empty@390` and `settings-policy/default@390`.
   - **Kraft-wzclr (pre-existing):** Policy@390's counter column squeezes `verify_fix_loop` to "verif y_fi…". It is identical in a shot from the build before the follow-up, and not flagged.

### D · Documents · 0 on a node with sessions
- **Cause:** the agent side, not the UI filter or the ingest. `ingest_session_summary` skips a session with a NULL `session_summary_ref`, and only `kind: agent` hooks write one.
- **Evidence** (live `~/.kraft/run/orchestrator.db`, done sessions with a ref):

  | hook | kind | with a ref |
  |---|---|---|
  | `on.test.run` | subprocess | 0 / 297 |
  | `on.review.mr.run` | builtin noop | 0 / 572 |
  | `on.env.prepare` | builtin | 0 / 94 |
  | `on.ci.poll` | forge | 0 / 84 |
  | `on.implementation.start` | agent | 191 / 201 |

  Where verify's agent sessions did write refs, their summaries are indexed on verify (40 on `e983d85c`), so the filter is right.
- **Bead:** **Kraft-1s6u2**.
- **UI:** when the scoped count is 0 and the scope has sessions, the Documents tab's hint (its `title`) reads `Documents · 0 · N sessions have no summary`. `Tabs` gained an optional `hint`.
- **Not reproduced in the sweep:** no fixture node has sessions and no documents.

### E · Handoff refresh
- **Frames copied** from the final sweep into `design/handoff_v4/screens/`:
  - `d07-board-selection` ← `board/selection-bar@1280`
  - `m03-repo-sheet` ← `board/repo-sheet@390`
  - `d34-timeline-rounds` ← `item-timeline/capped@1280`
  - `d35-timeline-gate` ← `item-timeline/long@1920`
  - `m10-timeline` ← `item-timeline/long@390`
  - `d46-settings-chains` ← `settings-chains/editor@1280`
  - `d54-analytics` ← `analytics/default@1280`
- **README:** §1's Timeline sentence now says "rounds only on the left; sessions are rows in the right pane". The chains and analytics lines are gone from §6.
- **Noted:**
  - `m10-timeline` is the phone item page. Phone has no Timeline tab (Kraft-b9tu5), so the frame shows the chain list, not rounds.
  - §6's chains line is removed as asked, but that item is still open as Kraft-059md.
  - `design/handoff_v4/` is untracked in git and was not committed: staging it would add the whole handoff folder, zips included, to the repo. No E commit.

`ui-waves` fast-forwarded to `wave/W14` (`df0085e2`).

### W14 follow-up · Documents empty state, and the MR
- **Empty state:** `W14.D: the Documents list says why it is empty` (on `wave/W14`, merged into `ui-waves`). When the scoped count is 0, visible muted text sits under the list (the Timeline gap row's `stream-gap-label`): "No documents", then either "N sessions ran without a summary · see Tasks" (Tasks switches the tab) or "This node has not run yet". It replaces "no linked documents yet"; the tab hint stays.
- **Tests and sweep:** unit tests cover both branches. `node sweep/wave.mjs W13` re-run: 5/5 rules, 0 regressions. No fixture node has 0 documents, so no sweep cell shows the empty state.
- **Beads:** Kraft-059md closed as accepted (native selects stay). Kraft-1s6u2 re-scoped to "Non-agent hooks do not write summaries — by design; UI explains it".
- **MR:** `main`'s `frontend/` matched the W13 tip, so the W14 commits were cherry-picked onto `origin/main` as `ui-waves-w14`; its `frontend/` is identical to `wave/W14`. Branch checks: `npm ci`, tsc, 713 unit tests and build all pass. No local e2e run; CI's `frontend-e2e` covers it. **!224** opened against `main` (`release::patch`, squash, delete source branch).
- **Merged:** !224's pipeline 2847730490 passed (`frontend`, `frontend-e2e`, `release-impact`), so the merge went through as soon as it was requested, at 2026-09-14T15:05Z: squash `b0883700`, merge `b3b51010`, source branch deleted. `main`'s `frontend/` equals `wave/W14` plus `01652a5f` (PeekPane shows the first node for a never-started item, from `fix/peek-pane-not-started-node`), which landed on `main` separately. The local MR worktree and branch are removed.
