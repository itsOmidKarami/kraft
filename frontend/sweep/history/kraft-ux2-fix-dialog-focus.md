# ux2-fix-dialog-focus · the document dialog takes focus and has a Close button (Kraft-9d8b2.23)

Branch `kraft/ux2-fix-dialog-focus`, on `feb6b6086`. **Sweep port: 4394.**

Baseline: `node sweep/wave.mjs ux2-W5 --baseline` on this worktree with the change removed (applied back after).

## `node sweep/wave.mjs ux2-W5`

7/7 rules pass · 2 changed · 0 cleared · 0 regressions · 122 cells identical. The two changed cells are `ng-item-doc/artifact` at 1280 (and its twin): the document dialog's footer now holds a Close button. No other cell moved, so the focused-dialog outline reset (`.dialog:focus-visible`) shows nowhere.

`waves/ux2-fix-dialog-focus.json` scopes `ng-item-doc` for a re-run; it has no case file because no new cell was needed.
