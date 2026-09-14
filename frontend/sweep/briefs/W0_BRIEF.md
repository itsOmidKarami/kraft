# W0 — Item page layout · build brief for Claude Code

## Session config

Fresh thread. Latest **Opus**, extended thinking on, effort **high**. Write access: `frontend/src/**`, `frontend/sweep/**`, `frontend/e2e-shots/**`. One PR, branch `w0-item-layout`. Expect 1–2 hours including the re-shoot.

## Prompt (paste verbatim)

```
You are fixing wave W0 of the Kraft UI punch list: the item page layout. Read frontend/e2e-shots/sweep/PUNCHLIST-v3.md § W0 first, then this brief; where they differ, this brief wins. Evidence PNGs are under frontend/e2e-shots/sweep/. The design spec screens are in design/handoff_v3/screens/ (desktop-12-item-tasks, desktop-38-item-progress, desktop-41-config-corrected, desktop-48-scroll-model, desktop-43-log-maximized-corrected).

Scope is the item page only: frontend/src/views/work_item/** and the shared helpers named below. Do not touch the board, settings, or phone-only files except where a rule says so. Do not refactor beyond what a rule needs. Keep existing tests green and add the ones listed.

## The two defects

A. With real data (long description, submodules) the hero + the inline repos panel + gate card + action bar fill `.detail { overflow:hidden }` and `.item-split` measures 0×0. Evidence: el-inspector/gate-documents-long@1920.png (collapsed:true), item/gate-long@1280.png.
B. With default data the split renders but gets ~239px. Evidence: el-inspector/gate-changes-default@1280.png, item-changes/default@1280.png.

## Rules (each is a checklist item; tick all)

1. HERO IS A FIXED-HEIGHT BLOCK (views/work_item/Header.tsx + work_item.css)
   - Title: 2-line clamp (-webkit-line-clamp: 2). The existing headCollapsed wheel behaviour stays.
   - Description: render as markdown (use the same renderer RightPane/Doc.tsx uses — do not add a dependency). Clamp to 2 lines. A "more" button expands it in place; "less" collapses. No raw `##` or `**` may ever be visible. Expanded state does not push the split below its floor (rule 3); when it would, the description becomes its own scroll area capped at 40vh.
   - Repos: DELETE the inline `<section className="repos-panel">` from views/work_item/index.tsx. The hero keeps one chip: "+N submodules" (N = repos.length − 1; omit when 0). Clicking it opens the Config tab scrolled to the new Repos section (rule 7).
   - Progress bar (item.progress present): full width under the title on its own row, 3px segmented bar, label "Task 3 of 6 · <title>" one line, ellipsis. Never a second column beside the title. Evidence: el-hero/rate_limited-tasks-long@1280.png.

2. LAYOUT (index.tsx + work_item.css)
   - Fixed block = Header + ActionBar + GraphSplit's graph. All `flex: none`.
   - `.item-split { flex: 1 1 0; min-height: 320px; }` inside `.detail.item-page { display:flex; flex-direction:column; height:100%; overflow:hidden }`.
   - If the fixed block + 320px exceeds the viewport height: first the gate card collapses its findings/judge-note lines to one line each with "+N more"; then the description clamps to 1 line; then the stage graph shrinks to its 48px minimum. The split never goes under 320. Implement with a ResizeObserver on `.detail` that sets `data-fit="tight"|"tighter"|"tightest"` and CSS for each step. Do not use JS to set pixel heights.
   - `.item-split` keeps its two independent scrollers (inspector, right pane); nothing else on the page scrolls.

3. GATE CARD (ActionBar/GateCard.tsx + css)
   - Content column max-width 720px. Above 1280 the findings list and the judge note sit side by side (grid 1fr 1fr). Evidence: el-gate-card/gate-changes-default@1280.png (content hugs 200px of 964).
   - Deferred findings, concerns and judge note each collapse to one line + "+N more" at data-fit="tight" or narrower.

4. DURATION (format.ts + every caller)
   - Replace `elapsed(ms)` callers with `elapsedBetween(fromIso, toIso | null)`: to = null means now; result is clamped ≥ 0; format `52s`, `4m`, `1h 21m`, `2d 3h`. Keep `elapsed(ms)` exported for the log pane but clamp it too.
   - Semantics (user decision): node duration = run time, FROZEN when the session exits. For a node whose latest session has exited (gated, paused, capped, question, failed) show its wall time; do not keep counting. The gate card / action bar show waiting time separately: "waiting 3h 12m" from the gate_requested / needs_human event.
   - The hero, the split header, the stage-graph tooltip and the phone stage list must all call the same helper with the same inputs. Evidence: item/running@1280.png shows -31317s in the hero and -31329s in the split.
   - Add a unit test: exited session → frozen; running → counts; session start in the future → "0s"; negative input → "0s".

5. COMPLETED / ARCHIVED HERO (Header.tsx)
   - Show the last node ("open_mr") and "completed 2h ago" / "archived · read-only". Never "—" and never "· not started" on an item with status completed/abandoned/archived. Evidence: el-hero/done-tasks-long@1280.png, item/archived@1280.png.
   - Also: a never-started item shows "not started" without a "paused" chip beside it. Evidence: item/not_started@1920.png.

6. STAGE GRAPH SHIELD (StageGraph.tsx)
   - The current node shows the gate shield whenever item.pending_gate is set, regardless of chain length or template. Evidence: el-stage-graph/gate-tasks-long@1280.png (no shield) vs gate-tasks-default@1280.png (shield). Find why the long fixture loses it; fix the derivation, not the fixture.

7. CONFIG → REPOS SECTION (Inspector/Config.tsx)
   - New section "Repos" at the bottom of the Config tab: root_merge_policy line, then one row per repo: glyph, name, path (left-ellipsized), role, merge rank, state. Same Row component the deleted panel used. Anchor id "config-repos" so the hero chip can scroll to it (scroll the inspector scroller, not the window).

8. TASKS TAB COUNT (Inspector/index.tsx + Inspector/Tasks.tsx)
   - User decision: the tab counts SESSIONS. Tab label "Tasks · N" where N = sessions for the selected node. The pane's eyebrow reads "SESSIONS · N" with the same N. Remove the "1 task · newest first" line if it counts something else. Evidence: el-inspector/gate-tasks-default@1280.png ("Tasks · 6" over "SESSIONS · 1").

9. OVERFLOW MENUS (Header.tsx + ActionBar/index.tsx)
   - The header `…` button and the action-bar `…` button currently do nothing (verified by hand). Wire both to one Menu component (there is one in components/ui.tsx if `Menu`/`Popover` exists; otherwise a minimal role="menu" with arrow-key focus and Escape) holding: Archive (or Restore when archived), Open worktree, Copy id, Copy link. The action-bar `…` appears on every item state, not only done. Toast on Copy.
   - aria-haspopup="menu", aria-expanded, and the trigger keeps focus after Escape.

10. DENSITY (nocturne.css + work_item.css)
    - User decision: comfortable density applies everywhere. Item page hero meta, action bar height, inspector rows, log line-height and timeline rows read `--density-row-pad` / `--density-body-size`. Evidence: item/gate-comfortable@1280~comfortable.png pixel-identical to compact.

11. TITLE EDIT (Header.tsx)
    - The inline title editor becomes a textarea that auto-grows to 3 lines, Enter saves, Shift+Enter newline, Escape cancels. Evidence: item/title-edit@1280.png.

12. HEALTH BUBBLE (components/AppNav.tsx)
    - The degraded-health bubble gets a slot above the sidebar footer instead of overlapping it. Evidence: board/long@1280.png. (One-line CSS change is fine; this is the only allowed edit outside work_item.)

## Harness changes (frontend/sweep/) — do these FIRST so the acceptance run is meaningful

- checks.ts: add `negativeDurations`: count of text nodes matching /-\d+s\b/ with up to 8 examples. collect.mjs and the manifest flag it as "neg-duration".
- fixtures.ts: settingsFor("empty") must return empty repos/hooks/templates/steering/sessions (today it does not).
- elements.spec.ts: keep `element.collapsed`; add `element.box.height` to the manifest entry if not already there.

## Acceptance (run these; paste the numbers into the PR description)

  cd frontend && npm test -- --run
  SWEEP_SCREEN=item,el-,composer,flow-inspector,flow-log,flow-graph,flow-item-tabs npx playwright test -c sweep/playwright.sweep.config.ts
  node sweep/collect.mjs

Must hold:
- every el-inspector and el-right-pane cell: collapsed:false AND box.height ≥ 320
- neg-duration count = 0 across ALL cells
- flow-inspector-tabs-selection, flow-log-maximize, flow-graph-resize, flow-item-tabs-keyboard: last step reached, step.error null
- item/gate-long@1280: gate card, Approve/Reject, stage graph and both split panes visible in the frame without scrolling (look at the PNG)
- item/gate-comfortable@1280~comfortable differs from item/gate-comfortable@1280 (pixel diff > 0)
- menu/header-more@1280 and composer/overflow-menu@1280 show an open menu with four items
- no new console errors

Then write frontend/e2e-shots/sweep/W0-RESULT.md: rules ticked, numbers above, anything you could not do and why, and the list of cells that still look wrong to you (with reasons) so the next wave can pick them up.
```

## After it finishes

Copy `W0-RESULT.md` and the re-shot `e2e-shots/sweep/` into this project (same `sweep-shots/` path; the manifest dedupes by id) and I'll diff before/after per cell and sign off the merge.
