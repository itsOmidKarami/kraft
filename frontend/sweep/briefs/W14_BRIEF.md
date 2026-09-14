# W14 build brief — closing items

Branch `wave/W14` on `ui-waves` HEAD. One commit per section. Acceptance: `node sweep/wave.mjs W13` (Timeline/Documents cells) and `node sweep/wave.mjs W11` (chains/analytics/board cells) both ALL PASS, then full sweep, collect, append RUN-SUMMARY.md, stop.

## A · Timeline left pane = rounds only

`Inspector/Timeline.tsx`: one 44px row per round — `round N · HH:MM → HH:MM · Xm`, right-aligned `S sessions · F findings · judge: <verdict>` (or `running`). No fold, no child session rows. Node-level events outside a round stay as single rows; drop `node_started` / `node_completed` when within 60s of a round boundary (they are the boundary). Escalation groups stay one row `escalation · turn N`. Selecting a round scopes the right pane (exists). In the right pane (`RightPane/Events.tsx`) session rows become selectable: highlight, `selection=session:<id>`, Up/Down move, Enter opens `view log`. Update W13's render test: no session rows in the left list; a right-pane row click sets the selection.

## B · Chains editor inputs (W12.3, verify then finish)

`settings/Chains*.tsx`: text inputs `max-width: 320px`; `gate_after`, `fix_loop`, `reject_to` use the app `Select` (as Appearance/Policy), not native `<select>`. Check `settings-chains/editor@1280` afterwards — the current frame still shows native selects and a 320px `id` field with 540px free; if the W12 commit exists but the frame is stale, say so in RUN-SUMMARY and re-shoot.

## C · Harness

1. `sweep/fixtures.ts analyticsFor`: `done: Math.max(0, 9 - i)` (negative DONE counts in the `long` variant).
2. Re-add the two cells v4's README references: `board/selection-bar@1280` (select two done rows → selection bar) and `board/repo-sheet@390` (long-press a row → repo sheet). If those interactions were renamed in round 3, alias the new cases to these ids in `sweep.spec.ts`.
3. `input<16` calibration: treat computed `font-size ≥ 15.5px` as passing (Safari's zoom threshold is 16 after rounding); add a checks.spec case. If any phone input is genuinely < 15.5, fix the input, not the check.

## D · Documents count = 0 on a node with sessions

`item-documents` shows `Documents · 0` for a verify node with 8 rounds of sessions while Tasks shows 30. Find out whether the sessions' `session_summary_ref` is null (agent wrote no summary), the ingest skipped them, or the `this node` scope filter drops them. Fix the UI side if it is the filter; if it is ingest or the agent, file a bead with the evidence and show `Documents · 0 · N sessions have no summary` in the tab hint so the zero is explained.

## E · Handoff refresh

After the full sweep, copy these frames over `design/handoff_v4/screens/`: `d07-board-selection.png` ← `board/selection-bar@1280`, `m03-repo-sheet.png` ← `board/repo-sheet@390`, `d34-timeline-rounds.png` ← `item-timeline/capped@1280`, `d35-timeline-gate.png` ← `item-timeline/long@1920`, `m10-timeline.png` ← `item-timeline/long@390`, `d46-settings-chains.png` ← `settings-chains/editor@1280`, `d54-analytics.png` ← `analytics/default@1280`. Update README §1 Timeline sentence: "rounds only on the left; sessions are rows in the right pane". Remove the chains and analytics lines from §6 Open beads.

Rules unchanged: never weaken a check or rule; look at the cells; QUESTIONS.md only for true blockers.
