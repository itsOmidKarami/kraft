# ux2-W17 · The phone, PR 1 (A–H)

Branch `ux2/W17` pushed as `kraft/ux2-W17`, on `cc643d854` (the `set_harness` op, #393), after W14, W15, W16, the sweep split (#383), W18 PR 1 (#388) and the `<main>` landmark test (#391). PR 2 (search, analytics, More, the nine areas) is `ux2/W17b`, cut from main after this merges.

**Sweep port: 4399** for the baseline and every run. The baseline is `node sweep/wave.mjs all --baseline` shot from a clean detached checkout of `cc643d854` with no `e2e-shots` (1843 cells), copied to `e2e-shots/baseline/all` and `…/ux2-W17`.

## `node sweep/wave.mjs ux2-W17`

10/10 rules pass · 110 cells in scope · 95 changed (94 new, `ng-shell/board-stub@390`: the redirect to the shipped board is gone, the phone board shows), 1 cleared (that cell's old flag), 0 regressions.

| Rule | Result |
|---|---|
| target<44 = 0 on `^ng-phone` | pass |
| input<16 = 0 on `^ng-phone` | pass |
| overflow-x = 0 on `^ng-phone` | pass |
| offscreen = 0 on `^ng-phone` | pass |
| clipped-v = 0 on `^ng-phone` | pass |
| contrast = 0 on `^ng-phone` | pass |
| no console errors on `^ng-(phone-(?!login\|board/offline)\|shell)` | pass |
| flow steps complete on `^flow-ng-phone` | pass |
| nested scrollers ≤ 2 on `^ng-phone` | pass |
| no newly flagged cells | pass |

New cells (all at 390): `ng-phone-board` 9, `ng-phone-item` 18, `ng-phone-composer` 6, `ng-phone-node` 10, `ng-phone-task` 7, `ng-phone-gate` 4, `ng-phone-doc` 2, `ng-phone-new` 4, `ng-phone-login` 3, `ng-phone-shell` 1, and the flows `ng-phone-back`, `-sheet`, `-steer-running`, `-gate-approve`, `-resize`, `-no-redirect` (30 step rows). The excluded console errors are the pre-sign-in 401 on the login cells and the refused list read that is the offline cell.

## `node sweep/wave.mjs all`

1/1 rule passes · 1937 cells · 94 new · 1 existing cell changed (`ng-shell/board-stub@390`) · 0 newly flagged. Over this PR's life `ng-chains/gate@1280` and `ng-chains/node-empty@1280` twice recorded a setupError (an 8 s wait for `.canvas`) and passed on the next run; the same cell fails 1 in 6 on a clean checkout of main (bead filed under Kraft-9d8b2).

## What the sweep and a browser found that the unit tests did not

- A bordered list in the content column was squeezed to nothing when the content was taller than the screen (`overflow: hidden` lets a flex item shrink): `.ph-content > * { flex-shrink: 0 }`. The gate review's file list was unreachable.
- The `more` link and the `all` chip were narrower than 44px; the FAILED tag (4.39:1) and the log times (4.11:1) missed 4.5:1.
- An effect that returned `scrollIntoView`'s value threw "is not a function" on unmount in a real browser, blanking the item screen after Back from a node.
- The desktop sign-in card at 390 fails overflow-x, target<44 (×3) and input<16: the phone has its own.
- `getByRole("button", { name: "Approve" })` matches a card whose text says "approve …": flows use `exact: true`.

## Exit test (real server)

A throwaway `kraft` (own `KRAFT_HOME` in the worktree's `.dev/`, port 8772, the built SPA, the fake agent, seeded work items), driven by Playwright at 390 and checked against the API:

- Board: 4 cards, a Needs you badge of 2, `/ng/` stays on `/ng/`.
- Gate: opened from the board, Review and decide shows the document, Approve moved `spec_approval` to `plan_approval`.
- Paused item: Steer resumed it with the note (`running`); Pause from the sheet paused it.
- Steer on a running item: the page sent `POST …/pause` then `POST …/resume`, in that order, and the item was running again.
- New work item: Create paused made a paused item with the typed title.
- No console errors or page errors in any step.

## Differences from the mobile prototype

A `cap` stop's card says Open and its pair is Steer · Retry, not Raise cap (the item API reports no running-time cap to raise, Kraft-x8qzu); Raise is the dollar budget. The gate pair's primary is "Review and decide". Node and task have no YAML tab (Kraft-9d8b2.20). No Steer when `steerable` is false (#387). The status bar, notch and home indicator are not drawn.

## Shared files touched

`App.tsx` (the phone branch and the phone sign-in), `boot.tsx` (the redirect removed), `css.contract.test.ts` (two phone files in the `<main>` allow-list), `sweep/cellKit.ts` and `sweep/sweep.spec.ts` (`Case.noLight`, one condition). `ng/legacyPath.ts` stays: the sidebar's "Current UI ↗", the placeholder and the item header still use it.
