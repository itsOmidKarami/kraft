# ux2-W17b · The phone, PR 2 (I–Q)

Branch `ux2/W17b` pushed as `kraft/ux2-W17b`, on `736eb5799` (#397, the cap stop's `limit` and the desktop raise dialog), after PR 1 (#395), the Inter webfont (#396), W18 PR 1 (#388) and W14–W16. PR 1 is the stack, board, item, node, task, gate review and new item; this PR is search, analytics, More and the nine areas, the screen list and its reach walk, and Raise cap.

**Sweep port: 4399** for the baseline and every run. The baseline is `node sweep/wave.mjs all --baseline` shot from a clean detached checkout of `736eb5799` with no `e2e-shots` (1955 cells), copied to `e2e-shots/baseline/all` and `…/ux2-W17`.

## `node sweep/wave.mjs ux2-W17`

10/10 rules pass · 228 cells in scope (PR 1's 110 and 118 new) · 0 regressions. The changed cells beyond the new ones are PR 1's own `ng-phone-item/*` and `flow-ng-phone-*` rows: the item's brief "more" link now sits tight under its text (it was 30px away) and the status chips pass contrast.

| Rule | Result |
|---|---|
| target<44 = 0 on `^ng-phone` | pass |
| input<16 = 0 on `^ng-phone` | pass |
| overflow-x = 0 on `^ng-phone` | pass |
| offscreen = 0 on `^ng-phone` | pass |
| clipped-v = 0 on `^ng-phone` | pass |
| contrast = 0 on `^ng-phone` | pass (two fixes below) |
| no console errors on `^ng-(phone-(?!login\|board/offline\|area/policy-stale)\|shell)` | pass |
| flow steps complete on `^flow-ng-phone` | pass |
| nested scrollers ≤ 2 on `^ng-phone` | pass |
| no newly flagged cells | pass |

`ng-phone-area/policy-stale` is out of the console rule because its 409 is the cell: the browser logs a failed load for any 409.

New cells (all 390): `ng-phone-search` 4, `ng-phone-analytics` 4, `ng-phone-more` 6, `ng-phone-area` 55 (chains, library, harnesses, repos, policy, auto-intake, notifications, access, appearance, about), `ng-phone-item/capped-limit` and `capped-raise`, and the flows `ng-phone-reach` (one row per screen of `phone/screens.ts`, 30 screens), `-area-publish`, `-area-stale`, `-access-restart`. `ng-phone-shell/soon` became `ng-phone-shell/not-found`: the Soon placeholder is deleted.

## `node sweep/wave.mjs all`

2073 cells · 119 new · no newly flagged cell except `ng-chains/gate@1280` and `ng-chains/node-empty@1280`, the 8 s `.canvas` wait that flakes on main too (Kraft-9d8b2.31). One shipped cell moved 1px: `flow-log-maximize/03-wheel-up-pauses-follow@390`, a scroll offset that differs run to run, in a page this PR does not touch. No desktop `ng-*` cell changed.

## What the sweep and a browser found that the unit tests did not

- Two contrasts: the muted status chip on the line colour (4.24:1, now the secondary text colour) and the `problem` chip on the line colour (4.39:1, now an outline with no fill).
- A link row was underlined as a plain anchor: `a.ph-row` resets it.
- The Policy default-and-maximum value wrapped mid-phrase: it breaks after the default.
- `getByLabel` finds the sheet's dialog before its field: the flows use the field's aria-label, and `Publish` is `exact` because "Review & publish" contains it.
- The reach flow found the wrong card when two board items shared a title: a card is found by its bead.
- `tsc -b` (CI) caught what `tsc --noEmit` did not: the editor's `menu` kind has no `set`.

## Exit test (real server)

A throwaway `kraft` (own `KRAFT_HOME` in the worktree's `.dev/`, port 8773, the built SPA, the fake agent), driven by Playwright at 390 and checked against the API:

- Every area reached by tapping More → the row, from the board.
- Policy: edit Housekeeping › max active items, Review & publish, Publish: the chip said published and `GET /policy` held the new value (3 → 4).
- Auto-intake: Add schedule opened `/schedules/0`; Remove schedule asked first and went back; the draft was discarded.
- Notifications: link back and webhook URL saved on change (`GET /notify` held them, never the URL); Remove URL asked first.
- Appearance: Moss saved to `theme.yaml` and repainted `<html>`; Graphite put it back.
- Access on loopback says hosts, password and sessions are not used; About says unknown with no feed; Repos lists the connected repo and opens it; a chain opens.
- No console or page errors in any step.

## GAP §5.1: the seven phone-vs-desktop rows

| Row | Resolved | Where it is pinned |
|---|---|---|
| Item running: Steer | Pause, then resume with the note | PR 1: `flow-ng-phone-steer-running`, `Item.test.tsx` |
| Node running | Pause · Skip while running, Retry once stopped | PR 1: `Node.test.tsx` |
| Task | Thread tab with an escalation | PR 1: `ng-phone-task/thread` |
| New work item | Create paused beside Create and start | PR 1: `NewItem.test.tsx` |
| Analytics | 7 days; each tile maps to a field | `Analytics.test.tsx`, `ng-phone-analytics` |
| More | About row | `More.test.tsx`, `reach.test.tsx` (about) |
| Library | Steps tab | `Library.test.tsx` ("four tabs, Steps among them") |

## Differences from the mobile prototype

- A cap stop with a `stop.limit` (R73) offers **Raise the <noun>…** as a link in the state card, so the action bar stays one pair (Steer · Retry); the sheet is Cancel · Save & retry. Without `stop.limit`, Steer · Retry only.
- Notifications has no YAML of the file: it shows the effective values, and the URL as `(set, never shown)`.
- Access reads the environment lock from health against the saved value (the API has no flag): "Set by the environment: running on …".
- A new password signs every session out, this one too (the server revokes all), not "other sessions".
- The Policy findings chips are the server's three severities, not four.
- A scheduled item is always filed paused: shown as a fixed row, not a switch (the trigger code has no other state).
- The status bar, notch and home indicator are not drawn.

## Shared files touched

`phone/**` only, plus: `ng/css.contract.test.ts` and `ng/phone/contract.test.ts` allow-lists for what the phone reads (settings policy and intake models, `review/prefs`, `shell/aliases`); the restart call now lives in two places (More and Access), each behind its own confirm. No desktop page, `ng/ui` or `ng/theme` changed.
