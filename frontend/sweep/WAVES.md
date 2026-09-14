# WAVES — the full fix plan, one Claude Code session, run back to back

Context files, all in this repo: `frontend/sweep/briefs/PUNCHLIST-v3.md` (the evidence-backed list; every wave below is a section there), `frontend/e2e-shots/sweep/<screen>/<cell>.png` (evidence), `design/archive/handoff_v3/screens/*.png` (spec), `design/archive/handoff_v3/README.md` (spec rules).

## The loop (repeat per wave, in this order: W0 W2 W1 W5 W3 W4 W6 W7 W8 W9)

```
git checkout -b wave/<Wn>
node sweep/wave.mjs <Wn> --baseline          # snapshot before touching code
# implement the wave's rules below
npm test -- --run                             # unit tests green, add the ones the wave names
node sweep/wave.mjs <Wn>                      # re-shoot + pixel diff + acceptance → e2e-shots/DIFF-<Wn>.md
# read DIFF-<Wn>.md: fix FAILs and regressions, re-run until ALL PASS
# look at every PNG in "Still flagged" and "Regressions" yourself; a passing rule is not a passing wave
git add -A && git commit -m "<Wn>: <title> — sweep ALL PASS"
```

Stop conditions: a rule needs a design decision this file does not give → write it to `frontend/sweep/briefs/QUESTIONS.md` (wave, rule, the two options, your recommendation), skip that rule, continue. Never stop the whole run for one rule. Never weaken a rule in `sweep/waves.json` to make it pass; if a rule is genuinely wrong, say so in QUESTIONS.md.

Harness edits (`frontend/sweep/**`) are allowed when a selector or fixture is wrong; say so in the commit. Never edit a check to hide a finding.

At the end append to `frontend/sweep/HISTORY.md`: per wave — rules pass/fail, cells changed/cleared/regressed, commits, open questions.

## Machine checks to add before W0 (sweep/checks.ts, collect.mjs)

- `neg-duration`: text nodes matching `/-\d+s\b/`.
- `contrast`: for every visible text node, WCAG contrast of computed color vs the nearest opaque ancestor background < 4.5 (3.0 for ≥ 24px). Count + 8 examples. Cheap approximation is fine (walk up until `background-color` alpha = 1).
- `focus-ring`: on flow steps, `document.activeElement` has no visible outline/box-shadow difference vs its blurred state. Record as flag on flow cells only.
- fixtures.ts `settingsFor("empty")` must actually be empty.
- sweep.spec.ts: every screen gets a `~light` shell variant at 1280 (currently three).

---

## W0 · Item page layout

Rules 1–12 exactly as in `frontend/sweep/briefs/W0_BRIEF.md` (read it in full). Decisions already made: submodules → Config → Repos section; description 2-line clamp + "more"; split floor 320px; `…` menu = Archive / Open worktree / Copy id / Copy link; Tasks tab counts sessions; node duration = frozen run time, waiting shown separately on the gate card; comfortable density applies everywhere.

## W2 · Tablet + breakpoint unification

1. Every `@media` in `frontend/src/**/*.css` uses only `(max-width: 767px)`, `(max-width: 1023px)`, `(max-width: 1279px)`, plus the single `(max-height: 719px)`. Grep before and after; the after-list must contain nothing else. `usePhone.ts` stays at 767.
2. Under 1024 on the item page: the split stacks — inspector becomes a horizontal tab strip (Tasks / Changes / Documents / Timeline / Config) above one pane that shows the inspector list *or* the right pane, toggled by a segmented "List / Detail" control; picking a row in the list switches to Detail. Spec: `desktop-47-item-tablet.png`.
3. Under 1024: board row grid `22px minmax(0,1fr) 120px`; mini-chain hidden; node label stays. Peek is an overlay with scrim (already required under 1280).
4. Under 1024: settings pages single column, label above field; the repos table becomes cards.
5. Between 1024 and 1279: item split columns `360px minmax(0,1fr)`; action bar wraps to two rows (primary + hint on row 1, secondary on row 2) rather than ellipsizing.
6. Add a test that greps the CSS for disallowed breakpoints.

## W1 · Light mode

1. `nocturne.css` (and each palette file): one `:root[data-mode="light"]` block per palette that redefines the *whole* set together: `--color-bg`, `--color-surface`, `--color-surface-2`, `--color-text`, `--color-text-muted`, `--color-text-faint`, `--color-border`, `--color-neutral-700..900`, chip backgrounds, and the status tints. No light-mode rule may redefine text without also defining the surface it sits on.
2. `tests/test_palette_contrast.py` (or a vitest equivalent): every text token × every palette × both modes ≥ 4.5:1 against its surface token; muted ≥ 4.5 against surface, faint ≥ 3.0 (faint is only used ≥ 24px or decorative).
3. `theme.ts applyTheme` sets `data-mode` before first paint (read from localStorage in `main.tsx` before React mounts) so there is no dark→light flash.
4. Evidence to clear: `board/default@1280~light`, `item/gate-light@1280~light`, `board-peek/gate@1280~light`, plus every new `~light` cell.

## W5 · Text overflow policy

One block in `styles.css` plus targeted fixes:
1. `.mono-id` utility: 32-hex ids render as `c744…7b11` via a `<ShortId>` component (full id in `title`, click copies). Use it in the item breadcrumb (replace with the title — id moves to the hero meta), peek header, search results, document list, session rows, timeline rows. No bare 32-hex string may render as a title or crumb anywhere.
2. `.path` utility: `direction: rtl; text-align: left; unicode-bidi: plaintext` with ellipsis so paths keep their tail. Apply to document paths, repo paths, diff tree tooltips.
3. Titles in lists: `-webkit-line-clamp: 2`; in modals/sheets: 3. Search snippets: strip markdown (`remark` → plain) before rendering.
4. Identifier columns (hook names, loop names, template ids, steering file names): `minmax(14ch, max-content)`; the value column is the one that shrinks. Settings label column: `minmax(120px, max-content)`, never a fixed px width.
5. Log header: `grid-template-columns: minmax(0,1fr) auto`; session id via `<ShortId>`; hook name full; chip row `overflow-x: auto`.
6. `control-hint`: always rendered at every width; `white-space: normal`, max 2 lines, no ellipsis. Remove whatever media query hides it at 1100.
7. Config form: `grid-template-columns: 160px minmax(0, 560px)`; never `justify-content: space-between`.
8. Timeline rows: timestamp column `auto`, message `minmax(0,1fr)`.
9. Search overlay: results list `overflow-y: auto; max-height: 60vh`; bead footer titles 1-line ellipsis with `title`.
10. Documents list: grid `24px minmax(0,1fr) auto auto`; title 2-line clamp; kind + date never wrap.
11. Add a vitest that renders `<ShortId>` and a `.path` span and asserts the visible text pattern.

## W3 · Phone

1. Phone header (`PhoneTopBar`): `‹ Board` + item title (1-line ellipsis). No repo, no id. 44px tall.
2. Hero on phone: title 2-line clamp; description behind "more"; meta row: repo · template · `<ShortId>`; progress bar full width.
3. `@media (max-width: 767px)`: `.btn, .log-chip, [role=tab], .sheet-row, .board-row, .facet` `min-height: 44px`; chip rows `overflow-x: auto; scroll-snap-type: x mandatory`; inputs `font-size: 16px`.
4. Every phone scroll container: `padding-bottom: calc(var(--bottom-nav-h) + env(safe-area-inset-bottom))`. Define `--bottom-nav-h` once in `BottomNav.tsx`'s stylesheet.
5. Sheets (`PhoneComposer`, `RepoSheet`, doc sheet): header = title + one Cancel + one primary; body = fields only. Subtitle is the item *title*, never the id. Sheet top respects the header (`top: var(--header-h)`).
6. Phone peek: a bottom sheet (60vh, draggable to full) with title, close ✕, `Open →`, state, last 4 log lines; long-press opens it, tap navigates (current behaviour); a second long-press on another row swaps the sheet. Evidence: `flow-peek-open-close/*@390`.
7. Phone node page: back label = item title; tab strip and list agree on counts (W0.8); swipe between tabs works or the hint text is removed; `long-press repo` either opens `RepoSheet` or the affordance is removed.
8. Phone intake modal: full-screen sheet; includes the overrides panel behind an "Advanced" disclosure; body scrolls; Create button pinned above the keyboard-safe bottom.
9. Composer submit button never sits under the bottom nav: composers are sheets (rule 5), bottom nav hidden while a sheet is open.
10. Settings on phone: per-repo rows → two-line cards; the "show done" select and "open items in" rows fully visible (rule 4).

## W4 · Board, peek, archive

1. Facet bar: chips `max-width: 160px` ellipsis, one row; after 8 chips a `+N` chip opens a popover with the rest. Sticky bar gets `background: var(--color-bg)` and a bottom hairline; group headers likewise, with `top: var(--facet-bar-h)`.
2. Row grid per spec 45: `22px minmax(0,1fr) 200px 150px`; title 1-line ellipsis at every width; under 1280 the mini-chain column drops.
3. Peek: `position: fixed; right: 0; width: min(520px, 45vw)`, opaque `--color-surface`, left shadow, ✕ inset 12px from the viewport edge. Rows never re-layout when the peek opens (already true) and are *covered* not clipped: remove any `overflow` on the row container that slices stage-minis. Under 1280 a 40% scrim; click-scrim closes.
4. Peek header: line 1 `<ShortId>` · state chip · `Open →`; line 2 repo · template. Never wraps.
5. Peek body scrolls; empty space below the log block is removed (`flex: 1` on the log block, `min-height: 0`).
6. Row focus: `:focus-visible` outline on the row only; child pills keep their own width (fix the `fix·1` full-width box).
7. Archive view: sort control is the app's `Select`, label "Sort" with a space; search input `max-width: 320px` on the same row. Empty archive / analytics tables render a single "nothing here yet" line, no header row.
8. Sidebar state (expanded/rail, open groups) is read from localStorage before first paint and survives reload. Evidence `flow-sidebar-toggle/04-reload-persists@1280`.
9. Archive action gives feedback: toast "2 items archived · Undo" for 6s.
10. Group by: template shows `default` / `quick-task` headings; if the control does not exist, add it next to group-by repo, else fix it.

## W6 · Composers, toasts, focus, post-action state

1. Toast: desktop top-right under the header, 8px inset, stack up to 3; phone above the bottom nav. Never over the control just pressed.
2. Composer textarea: `padding-top: 8px`, `scroll-padding-top: 8px`; auto-grow 3→10 lines.
3. Post-action: every action navigates/re-renders by the id it was invoked on, never by the response's id. After Approve/Reject/Steer/Answer/Raise: composer closes, gate card shows a "pending…" state until the store's next event confirms, then the new state. After Create paused: navigate to the *created* id and toast "Created · paused".
4. ⌘/Ctrl-Enter submits every composer; Escape cancels and returns focus to the trigger.
5. Search: arrow keys move an `aria-activedescendant`; Enter opens the *active* result; Escape closes and restores focus.
6. `:focus-visible` on every interactive element: `outline: 2px solid var(--color-accent-500); outline-offset: 2px`. Chips, template cards, skip-node buttons, sidebar group buttons, ✕ buttons, log filter chips, "Select all". Template cards also get a selected state (`aria-pressed`).
7. Focus moves into view: any programmatic focus calls `el.scrollIntoView({ block: "nearest" })` on the *nearest scroll container* (not the window; do not use `scrollIntoView` on `document.body`).
8. Inspector tab strip: roving tabindex, arrow keys move, Enter/Space select.
9. Intake modal opens with focus on the title field; body scrolls; "Advanced" disclosure reachable.
10. Cancel from a composer restores the previous scroll position of the page's scroll container.

## W7 · Settings

1. Every control has a visible label (`<label for>` or `aria-labelledby`); the Plugins "kind" toggle gets its word.
2. Plugins: hook name column `minmax(14ch, max-content)` (W5.4); per-repo rows: name (left-ellipsis path), then a second line with toggle + command field on phone.
3. Appearance: "open items in" and "show done" rows never clipped — the page scrolls (`overflow-y: auto` on the settings main), and segmented controls `max-width: 420px`.
4. Chains: `valid` status is a badge, not an input; "1 node" singular; YAML toggle and Add node work (write tests); template name column `minmax(14ch, 1fr)`; phone shows the editor after picking a template.
5. Policy / Intake / Access: label column `minmax(120px, max-content)`; severity chips use the chip tokens, not white.
6. Save: sticky footer bar with dirty indicator + Save/Discard visible at every scroll position; after save, the form re-reads the response (phone showed the old value).
7. With sidebar expanded at 1100–1279: settings content single column.
8. Repo add sheet: 8px gap between toggle and label; footer sentence complete.
9. `/settings` index: either a real index page (list of the 9 sections with one-line descriptions) or the crumb is non-link — pick the index page.

## W8 · Correctness and copy

1. Question item: render `needs_context_question` in the gate/stop card, full text, above the Answer button.
2. Documents: never render a "Summary" heading with no body; doc-modal shows the title once (header), body starts at the first real heading.
3. Timeline: every group has a node label; the terminal event group is labelled "completed" / "abandoned", never `—`.
4. Analytics: `weekly_merged` and `totals` share one date range; "nothing completed in this range" only when the total is 0.
5. Search: "N results · lagging index" uses the actual result count; the lag note is a separate muted line.
6. Escalation card: show the latest `escalation_message.message`; "no summary reported" only when there is no message either.
7. `/api/theme` and any other pre-auth fetch wait for the session check; no 401 on the login screen.
8. Never-started item: "not started" with no `paused` chip.
9. Breadcrumb: title, never id (done in W5.1; verify).

## W9 · Dead surfaces and no-op settings

1. Header `…` and action-bar `…` menus (done in W0.9; verify on every state).
2. Log modal: find its entry point (the design has a "pop out" on log lines); if none exists, remove the component and its tests; if it exists, make the sweep case open it.
3. Comfortable density (done in W0.10; verify board + item + settings + peek all change).
4. Group by template (done in W4.10; verify).

---

## Round-3 sweep additions (do after W0 and W2, before W1)

In `sweep.spec.ts`: `~light` for every screen at 1280; mid-scroll (50%) variants for board-many, item tabs long, chains editor; `short-many` data variant (3-word titles × 12 repos); an item with no description / no documents / no diff; a 200-event timeline; doc viewer and Config YAML scrolled; peek on an item whose last log line is 1800 chars. In `mockApi.ts`: mutate scenario state on POST (approve → status active + gate cleared; pause → paused; archive → archived) so post-action frames are real.
