# Kraft-9d8b2.16 · sweep split: one file per wave

Branch `kraft/sweep-split`, on `8280bc3b3` (W14 #376). Sweep ports 4396 (main baseline, branch) and 4397 (one branch pass); the baseline is `node sweep/wave.mjs all --baseline` from a clean detached checkout of `8280bc3b3`.

## Ids before and after

Dumped with `npx playwright test --list` (file:line stripped) and the wave rules read from `waves.json` / `waves/*.json`, then diffed as sorted lists.

| list | main | branch | `diff` of the sorted lists |
|---|---|---|---|
| cell ids (`sweep.spec.ts`) | 1121 | 1121 | empty |
| flow ids (`interactions.spec.ts`) | 57 | 57 | empty |
| wave rules (name + full JSON) | 27 | 27 | empty, byte-identical |

Order: only one screen's own order changed, `ng-chains`, whose `move-to-library` cell (W12's) now sits in `ux2-W12.ts` after the W10 cells. Order across files matters only for which case is a screen's first (it gets the `~light` cell at 1280); every screen keeps its first case, so the id lists are equal. The same comparison on the first pass (pre-W14, 1094 + 56 + 26) was empty too.

## `node sweep/wave.mjs all`, main vs branch

Baseline 1818 cells on main, branch 1818 cells. 267 flagged on the branch, 268 on main.

Three cells differed, none from the move:

- `flow-log-maximize/03-wheel-up-pauses-follow@390` and `@1280`: the same cell on main, re-shot against main's own baseline, differs by 4.35% and 4.74%. A mouse-wheel step, timing dependent.
- `ng-chains/node-empty@1280` (and the `ng-chains/gate@1280` setup error in the full run): `locator.waitFor` 8000ms timeouts. The baseline itself had `setup` on `node-empty`. Re-shot alone on the branch: clean, 0 pixels changed.

Newly flagged after the re-shoot of those: 0. Pixel diff against the baseline of the re-shot subset: only the wheel cell.
