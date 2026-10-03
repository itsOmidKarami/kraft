# UI fixes, batch 2

You are in a git worktree of the Kraft repo. Implement the second batch of UI
fixes below. The full spec is in this prompt.

Setup
1. `git worktree list`: the first entry is the MAIN checkout ($MAIN). Design
   reference (read only): $MAIN/design/handoff_uxv2/reference/Kraft Prototype V2.dc.html
   and $MAIN/design/**. Compare evidence (read only):
   $MAIN/.claude/worktrees/bridge-cse_01Kabcg2UxRLjT3CRnoJUs1j/frontend/sweep/compare/
   (DIVERGENCES.md plus out/**; regenerate out/ there if it's missing).
2. Save this prompt to ./ui-fixes/CLAUDE_CODE_FIXES_2.md and commit it first.
3. Batch 1 (PR 502) is merged. Branch from the latest main. Several items
   build on it (L1, D1, S5). Before starting, read the code for each item:
   if main already does what an item asks, mark it "already done" in the
   summary and skip it.
4. Use a dev server port no other agent is using.

Rules
- Scope: frontend/src/ng, plus backend only where an item says so.
- One commit per item, ID first ("LV-2: …"). Tests next to each change; run
  the unit tests after each one.
- Don't touch frontend/sweep/compare.
- If something isn't settled by the spec, list it under "Questions"; don't guess.

## Logs (item/panes/Log.tsx)
- LV-2 Follow must scroll the element that actually scrolls. Today LogBody
  scrolls the <pre> but .pane-body is the scroller. Either make the <pre> the
  scroller (bounded height inside the pane) or scroll the pane body. A log
  opens at its newest line.
- LV-3 While following, auto-scroll on new lines. Once the reader scrolls up,
  stop following and untick "follow". Scrolling back to the bottom (or
  ticking follow) resumes it.
- LV-4 Closing full screen returns focus to the "full screen" button. Keep it
  mounted, or refocus it on close.
- LV-5 A task that hasn't run keeps its tabs. Each says why it's empty, e.g.
  Log: "No log yet. It starts when the step before this one finishes." Skip
  task is reachable before the task runs.

## Sidebar
- SB-5 Add "About" as a nav row at the end of the Settings group
  (routes.ts: give About the settings group). It's where bind:port and the
  restart warnings live after S5.

## Board (ng/board/*)
- BD-1 "Review" on a gate row opens that gate's review directly (the brief
  beside the review), not the item page with the gate selected.
- BD-2 First-run (empty) board, as in the prototype: centred hero with the
  3-step diagram; "Or drive it from an agent session `kraft admin init`" with
  Copy; the setup panel with a suggested repo path, a probe checklist, and
  "Skip to chain →". "+ New work item" stays visible in the header.
- BD-3 A row stopped by a spend cap offers "Raise budget" (opens the budget
  limit), like the cap row offers "Raise cap".
- BD-4 Peek: add chain and repo rows. The waiting card explains the gate and
  offers "Review changes" (it opens the review). Recent uses the WI-2
  summaries. Keep the item title as the peek title.
- BD-5 Offline banner: drop the request line (no "GET /work-items?…").
- BD-6 Done rows show a ✓ glyph, and progress reads as finished.

## Work item (ng/item/*, ng/graph/*)
- WI-1 One page-level Esc handler with this order: review → picker →
  escalation card → full-screen log → doc viewer → side pane → node view →
  chain. It works wherever focus is on the page, unless a text field or open
  menu takes the Esc itself.
- WI-2 Recent (chain pane and board peek): fold started/running/finished pairs
  into one line, lead with what changed ("code_review is running on
  verification", "fix loop round 2 · the fix committed a change",
  "escalation answered"). At most ~5 lines, plus a link to the rest.
- WI-3 Node glyph by task kind: agent ✦, test ⚙, command >_, forge/MR
  git-pull. FIRST check a real item (`just dev`): if the real data carries the
  task kind and glyphs are right, the cube is a fixture problem. Fix
  sweep/ngItems.ts instead and say so in the summary.
- WI-4 Failed state card: add "work kept: branch … · N files · tests passing"
  and the fix action the card names (e.g. "Fix the token in Repos" jumps to
  the repo's settings). The canvas fits the whole chain on load.
- WI-5 Item loading state: reuse the board skeleton, not a blank area.
- WI-7 Spend line shows the time budget too: "spent $2.41 of $5.00 · 1h 12m
  of 8h". Keep the node pane's "ran" row.

## Gates and review (ng/review/*, ng/graph/GateView.tsx)
- GR-1 Esc on the review page goes back to the item, same as ×.
- GR-4 Gate view shows the auto-review pre-step when the gate has one:
  "auto_review → you", with "runs first when reached".
- GR-5 Gate banner action says "Review changes" (not "Open gate").
- GR-6 Review page brief: strip the duplicate H1 (use docBody, as DocViewer
  does). Add the "written by …" provenance line.
- GR-7 Gate pane: add the tests line ("tests ✓ 412") when there's a result.
- GR-9 At 900px the review footer buttons stay on one line (shorten labels,
  or move secondary actions into a menu).
- Keep as is: Approve stays disabled while must-fix threads are open (GR-2),
  and Reject… stays on the gate (GR-3). The prototype will be updated to match.

## Doc viewer
- DV-5 The close control is × in the header; remove the footer Close button.
  Goes with D1 (drawer).

## Settings (ng/settings/*)
- ST-1 Policy: scopes nested as boxes (work item ⊃ nodes ⊃ steps) under a
  "Limits" heading, with "Preview on a chain" beside it. Find out why the
  "set below policy" column drops at 900px, and keep it.
- ST-3 Auto-intake header shows the next check: "On · next check in 3 min".

## Done when
- Every item is implemented and committed, unit tests pass, typecheck and lint
  are clean.
- A separate sweep agent re-runs the compare; don't run it yourself.
- Final summary: one line per ID (done / partial / skipped + why), files
  touched, and "Questions".
