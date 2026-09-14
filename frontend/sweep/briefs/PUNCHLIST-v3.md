# Kraft UI punch list v3 — adjudicated (two independent passes)

1148 cells · pass A (`triage-b.json` predecessor) and pass B (`triage-b.json`) agreed on 764, disagreed on 384; every disagreement adjudicated and the result is `sweep-shots/triage.json` (**864 bug · 108 ux · 176 ok · 9 disputed**). Changes from v2 are marked **Δ**.

Per-wave cell counts (bug+ux): W0 439 · W5 152 · W3 97 · W7 85 · W6 59 · W4 55 · W8 50 · W2 22 · W9 10 · W1 3 (light mode was only shot 3 times; expect ~all screens in round 3).

Build order: **W0 → W2 → W1 → W5 → W3 → W4 → W6 → W7 → W8 → W9.** W0 fixes 439 cells in one PR and unblocks testing everything under it; W2 rewrites every stylesheet's breakpoints and must precede the phone/board CSS work.

---

## W0 · Item page layout — P1, blocker (439 cells)

**Δ Two defects, not one:**
- **0px split** (`long` data): the hero renders the full description as raw markdown, then lists every submodule; hero + gate card + action bar consume the whole `.detail { overflow:hidden }` and `.item-split` measures 0×0 at 1100/1280/1920 (`el-inspector/*-long`, `el-right-pane/*-long`: `collapsed: true`). Tasks, Changes, Documents, Timeline, Config, log, Maximize, graph handle and review buttons do not exist on screen. 5 of 16 flows die at step 1.
- **239px split** (`default` data): the split renders but gets the bottom third of the page — file tree sliced at `progress.py`, diff at line 4, config at `on_failure`, timeline after 3 events (`el-inspector/gate-changes-default@1280` box 420×239, `item-changes/default@1100..1440`, `item-config/default@1100..1280`).

Fix (`views/work_item/index.tsx` + `work_item.css`):
1. Hero fixed block: title 2-line clamp; description rendered as markdown, 2-line clamp, "more" expands in place; **no submodule list** (chip stays; the list moves to Config → Repos).
2. Hero + gate card + action bar + graph = `flex:none`; split = `flex:1 1 0; min-height:0` **with a 320px floor**. If the fixed block cannot fit, gate card findings/judge lines collapse to one line, then description to one line.
3. Progress bar goes under the title at full width (`el-hero/rate_limited-tasks-long@1280` squeezes the title into 280px).
4. **Δ Duration is wrong on every state including running**: hero prints `-31317s` while the split header prints `-31329s` for the same node and the phone list prints `now` (`item/running@1280`, `item/escalating@1920`, `el-phone-list/running-documents-long@390`). One `elapsed(from, to = now)` helper, clamp ≥ 0, format `1h 21m`; waiting nodes show "waiting 3h 12m" from the gate/stop event.
5. Completed/archived hero shows `—` + "· not started" (`el-hero/done-*`, `item/archived@*`). Show last node + "completed 2h ago" / "archived · read-only".
6. **Δ Gate card** content hugs the left 200px of a 964px card; judge-note banner stretches to 1600px at 1920 (`el-gate-card/gate-*-default@1100..1920`, `*-long@1920`). Cap the card's content column at ~720px, or lay findings and judge note side by side above 1280.
7. **Δ Stage graph**: current `review` chip has no gate shield on `long` items though the item is waiting on a gate (`el-stage-graph/gate-*-long@*`) — the shield is derived from something the long fixture lacks; find and fix the derivation.
8. Health-degraded bubble overlaps the sidebar footer (every `long` desktop shot).
9. Title edit is a single-line input that cuts the value (`item/title-edit@1280`) → textarea, auto-grow to 3 lines.

Acceptance: `collapsed:false` and `box.height ≥ 320` on every `el-inspector` / `el-right-pane` cell; `flow-inspector-tabs-selection`, `flow-log-maximize`, `flow-graph-resize`, `flow-item-tabs-keyboard` reach their last step with `error:null`; no `-\d+s` string anywhere in any frame (add this as a machine check).

---

## W1 · Light mode — P1, systemic (3 cells shot, all broken)

Two failure modes: near-white text on white (`board/default@1280~light`) and dark surface with the muted ramp gone invisible (`item/gate-light@1280~light`). One `[data-mode]` token set per mode defining surface + text ramp + border + chip-bg together; extend `test_palette_contrast.py` to every text token × palette × mode. Round 3 shoots `~light` for every screen.

---

## W2 · Tablet 768–1024 + breakpoint unification — P1 (22 cells)

CSS breakpoints today: 640, 767, 768, 900, 1023, 1200, 1279, 1439, 1440, `max-height:719`. Spec: 767 / 1023 / 1279. At 768–1024 the split never stacks: log pane 25–55px wide (`item/running@768`), Approve/Reject clip off-viewport (`item-documents/default@768`), repo names drawn over template chips (`settings-repos/default@768`). Under 1024 the inspector becomes a tab strip above one pane (spec 47); settings forms single-column. One PR, every stylesheet. **Δ** `new-item/empty@1024` is fine; the modal overflow is at 768 only.

---

## W3 · Phone — P1 (97 cells)

Evidence: `item/title-edit@390` (breadcrumb repo + "Board" + hex id overprint), `item/gate@390` (15–21 targets <44px; `More` off-screen), `phone-node/default@390` (back link = hex id; `Tasks · 11` tab over `SESSIONS · 1`), `board-peek/escalated-long@390`, `board/repo-sheet@390`, `flow-phone-node-page/02-swipe-tab`, `04-long-press-repo` (advertised gestures do nothing), `settings-appearance/default@390` ("show done" select sliced by bottom nav), `flow-gate-reject/03-type@390` (submit button sits on the bottom nav).

**Δ Phone peek is broken, not just cramped**: `flow-peek-open-close/01-click-row@390` opens full-screen with the raw hex id as header, no close, no Open link, ~250px dead space; `04-second-row@390` does not open a peek at all; `05-title-click-navigates@390` navigates nowhere. **Δ Phone new-item modal has no overrides panel** — budget/skip-node inputs silently go nowhere (`flow-new-item-full/06-budget@390`).

N1 (sheet action duplication): every composer sheet shows Cancel/primary in the header *and* body, with the raw hex id as subtitle (`composer/*@390`); `doc-modal/*@390` drops its primary actions.

Fix: phone header = back + title; 44px floor on `.btn`, `.log-chip`, tabs, sheet rows; horizontal-scroll chip rows; one action pair per sheet; node-page back label = title; gestures work or the copy goes; `padding-bottom: calc(var(--bottom-nav) + env(safe-area-inset-bottom))` on every scroll container; phone peek = sheet with title, close, Open →, and a tap on the second row opens it; phone intake modal gets the overrides panel behind an "Advanced" disclosure.

---

## W4 · Board, peek, archive — P1/P2 (55 cells)

Evidence: `board/long@1280` (repo facet chips wrap to 5 lines; **Δ** also `flow-new-item-full/00-start@*` and `flow-search-keyboard/00-start@*` — a 130px block with `payment-reconciliation-service` sliced, no `+N`), `board-peek/running-long@1100` (no scrim under 1280; rows cut mid-word at the peek edge), **Δ** `board-peek/gate@1920` (peek ✕ clipped at the viewport edge; row stage-minis sliced where the peek begins — the peek edge problem exists at every width), **Δ** `board/selection-bar@1100..1920` (sticky facet bar / group header slices the row beneath it with no fade), `flow-peek-open-close/03-esc-closes@1280` (focused row draws its `fix·1` pill as a full-width box), `archived/default@1280` ("Sortarchived date", native `<select>`, full-width search on its own row), `archived/empty`, `analytics/empty` (table headers over nothing), **Δ** `flow-sidebar-toggle/04-reload-persists@1280` (reload drops the expanded sidebar and open Settings group — persistence is broken).

Fix: facet chips `max-width 160px`, one row, `+N` after 8; row grid per spec 45; peek = overlay with scrim under 1280 and a proper right inset so its ✕ never clips; rows under the peek are covered, not clipped (peek background opaque, rows keep their width); sticky bars get a solid background + bottom hairline; row focus style scoped to the row; archive bar uses the app select; empty tables → one empty-state line; sidebar state persists (`localStorage` key is read on boot, not after first paint).

---

## W5 · Text overflow — P2, cross-cutting (152 cells)

Root causes:
1. Fixed-width columns clip at 1920: log header `log-sid`/`log-hook` (`item/capped@1920`), settings label column (`settings-policy/default@1920`, `flow-settings-save-roundtrip/00-start@1280`: `an_extremely_long_loop_name_that_no…`), steering file list 180px beside an 800px pane (`settings-steering/file-open@1280..1920`).
2. Grid children without `min-width:0`: Documents list titles cut at 11 chars with 400px free (`el-inspector/gate-documents-default@1920`) — **Δ also the phone node-page document list at 4–7 chars** (`flow-gate-approve/01-read-document@390`).
3. Identifiers truncated to 4 chars on phone (`on.e…`, `verify_fix…`, `Poll every…`).
4. Unclamped long text: doc-modal title 8 lines, search result title + raw-markdown snippet, new-item template chip.
5. **Δ `control-hint` is absent at 1100, not truncated** — `el-action-bar/gate-*-default@1100` shows 500px of empty bar and no hint; at 1280 it is cut to `imp…`; at 1920 it is full. The hint is hidden by a media query and ellipsized by a fixed width. It is the only sentence that says what Reject does / why the item stopped (`waiting@*: not…`, `budget@*: ca…`). Rule: always present, wraps to two lines, never ellipsizes.
6. **Δ Config values stranded** 700–1100px right of their labels (`item-config/default@1440..1920`, `el-right-pane/gate-config-default@1920`, and clipped at the left edge: `ORK ITEM`, `emplate`). Config form: label + value in a `max-width: 720px` two-column grid, not `justify-content: space-between`.
7. **Δ Timeline timestamps clipped at the pane's right edge** at 1100/1280 (`el-right-pane/gate-timeline-default@1100`).
8. **Δ Search overlay**: last result row sliced by the panel's fixed height at every width, session results titled with bare 32-hex ids, bead footer titles cut mid-word (`search/overlay-results@1100..1920`).

Rules (one block in `styles.css`): 32-hex ids middle-ellipsize and never appear in a breadcrumb or as a list title; paths ellipsize from the left; list titles 2-line clamp, modal titles 3-line; identifiers never truncate below 14 chars, value column yields first; label columns `minmax(120px, max-content)`; log header `minmax(0,1fr)` with a scrolling chip row; overlay result list scrolls inside the panel.

---

## W6 · Composers, toasts, focus, post-action state — P2 (59 cells)

- N2 toast covers the control just used and the bottom nav (`toast/after-approve@390`, `flow-gate-approve/03-toast-visible@*`).
- N3 filled textarea slices its first visible line (`composer/*-filled-long@*`).
- **Δ Post-action state is wrong, not just stale**: after Steer the app navigates to a *different* work item (`flow-pause-steer-resume/02-steer-open@1280`: pauses `ed12ff63…`, steers `6c17dcaa…`); after Create paused it lands on an unrelated running item with no toast (`flow-new-item-full/08-create-paused@*`); after Approve the gate card still offers Approve/Reject 3s later (`flow-gate-approve/04-after-3s`); after "Steer applied — resumed" the composer stays open with the note in it. Part of this is the mock returning a fixed id — but the navigation target comes from the response, so the app trusts a response id over the item it is on. Navigate by the id you acted on; show a pending state until the store confirms.
- ⌘-Enter does not submit the Reject composer (`flow-gate-reject/04-cmd-enter@*`).
- **Δ Search keyboard**: Enter opens the first result, not the one arrow-down highlighted (`flow-search-keyboard/04-enter-opens@*`).
- **Δ Focus rings missing** on: intake modal ✕, template chips (also no selected state on the chosen template), skip-node buttons, "Select all", "Read document", the sidebar Settings group button, log filter chips (`flow-new-item-full/01,04,05`, `flow-board-selection-archive/01`, `flow-gate-approve/01`, `flow-sidebar-toggle/03`, `flow-item-tabs-keyboard/03`). One `:focus-visible` rule on every interactive element; the inspector tab strip gets roving tabindex.
- Focus moves to an off-screen control without scrolling it into view (`flow-item-tabs-keyboard/03-tab-x20`).
- **Δ Archive selection gives no feedback** — checkboxes clear, no toast (`flow-board-selection-archive/03-archive@*`).
- New-item modal body does not scroll (`flow-new-item-full/07-scroll-bottom`, `new-item/scrolled@1280` cut at the top by the viewport).
- Peek body does not scroll and ends with 200px of dead space (`flow-peek-open-close/02-peek-scroll-bottom@*`).
- Cancel from a composer leaves the page scrolled past the hero (`flow-gate-reject/05-cancel-path@390`).

---

## W7 · Settings — P2 (85 cells)

- **Δ Plugins**: unlabelled toggle under the kind chips at every width, `on.implementation.sta…` truncated with room (`settings-plugins/default@768..1920`); per-repo names wrap 3–6 lines (`hook-runs@390`, `sidebar-open@*`).
- **Δ Appearance**: "open items in" row sliced by the viewport bottom with its hint overprinted at 1024/1100/1280 (`settings-appearance/default@1024,1100`, `palette-picked@1280`, `long@1100`, `sidebar-open@*`); mode/density segmented controls stretch to 850px.
- **Δ Chains**: `valid` status renders as an empty bordered input under "New"; "1 nodes"; YAML toggle and Add node do nothing (`settings-chains/long@*`, `flow-settings-save-roundtrip/05,06`); template name truncated to `a-very-long-tem…` beside a 300px empty column (`editor@1280`).
- Policy: fixed label column (see W5.1); severity chips render as white pills on save (`flow-settings-save-roundtrip/03-save@1280`).
- Save control is below the fold with no dirty indicator on screen (`01-edit-field@1280`); phone save shows the old value after "saved" (`03-save@390`).
- `sidebar-open@1100`: content squeezed; single column under 1280 with sidebar open.
- **Δ Harness/fixture**: `settings-access/empty`, `settings-plugins/empty`, `settings-chains/empty`, `settings-steering/empty`, `settings-appearance/empty` are not empty — the mock's `empty` variant still serves hooks/sessions/templates. Fix `fixtures.ts` `settingsFor('empty')` before round 3 so real empty states get shot.
- `settings-index@1280` lands on Repos; the "Settings" crumb leads nowhere. Accept, or make the crumb non-link.
- Repo add sheet: toggle and label touch; footer sentence trails off as "Repos → …".

---

## W8 · Correctness and copy — P2 (50 cells)

- **Δ Tab count vs pane count**: `Tasks · 6` tab over a pane reading `SESSIONS · 1 / 1 task` on every gate item (`el-inspector/gate-tasks-default@*`, `item-tasks/default@*`, `item/abandoned@1100`). The tab counts task_progress events, the pane counts sessions. Pick one and label it.
- **Δ Question item never shows the question** (`item/question@1920`: hint reads "nothing runs until you answer" but the question text is nowhere on the page).
- **Δ Documents: "Summary" heading with no body** at every width (`el-right-pane/gate-documents-default@1920`, `doc-modal/*`); doc-modal prints the title twice (header + first body heading).
- **Δ Timeline last group labelled `—`** instead of a node name (`el-fullpage/gate-timeline-default@1920`).
- Analytics "COMPLETED 30" beside "nothing completed in this range".
- Search prints `0 results · lagging index` above a populated list.
- Escalation card "no summary reported" above the message it refers to.
- `/api/theme` before auth → 401 on login.
- `paused` chip beside "· not started" on a never-started item (`item/not_started@1920`).

---

## W9 · Dead surfaces and no-op settings — P1/P2 (verified in dev, 0 disputed)

- **P1 Header `…` and action-bar `…` do nothing** — confirmed by hand in the dev instance (`menu/header-more@*`, `composer/overflow-menu@*`). The overflow only appears on done items; on active items there is no overflow at all, so whatever it was meant to hold (archive, open worktree, copy id) is unreachable. Either wire the menu or remove the trigger.
- `menu/doc-editor@*` → moved to W0 (the menu lives inside the collapsed split); re-shoot after W0.
- **Log modal never renders**: all 10 `log-modal/*` cells show the plain item page; confirm its entry point exists.
- **P2 Comfortable density has no visible effect on the item page** — confirmed by hand. Density tokens are applied to board rows only; item hero, action bar, inspector rows and log lines ignore them.
- **P2 Group-by: template** — the template *facet filter* works (user-verified). The group-by control, if it exists, produces no template section headings. Verify the control is present; if not, drop the setting from Appearance → Board.

---

## Accepted

`new-item/no-repos@1280` · `doc-modal/*` reachability · `nestedScrollers = 2` on item pages · `flow-inspector-tabs-selection/07-browser-back` → `about:blank` (harness) · `flow-sidebar-toggle/01-collapse@1100` (rail is the 1100 default) · `board/default@1024` facet wrap (adjudicated ok) · `el-phone-list/done-changes-long@390` (clean crop; the contradiction is in the hero cell).

---

## Round 3 sweep (after W0 + W2 land)

Add a machine check for `-\d+s` in any text node. Fix the `empty` settings fixture and a mutating mock for post-action states. Add: `~light` for every screen; mid-scroll positions; 3-word titles × 12 repos; item with no description / no docs / no diff; 200-event timeline; doc viewer + Config YAML scrolled; peek with an 1800-char log line; W9 verification cases (menu triggers by `aria-haspopup`, log modal by its documented entry point).
