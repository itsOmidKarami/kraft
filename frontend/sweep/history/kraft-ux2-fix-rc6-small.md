# kraft/ux2-fix-rc6-small

Kraft-9d8b2.50, .51, .52, .55, .56: small fixes from the 1.5.0rc6 test.

Cells (`cases/ux2-fix-rc6-small.ts`): `ng-review-readonly/ended@1280` (dark and light): a done item's review page, "Read only" in the bottom bar, no file comment button, no Finish review. `ng-item-unnamed-chain/running@1280` (dark and light): an item with `chain_template` null names the chain it runs ("default") in the pane heading. Flow `ng-review-number-click-then-enter` (`.flows.ts`): click the line number, the line group has focus, Enter opens the composer on that line, Cancel, click again, `c` opens it again.

Rules (`waves/ux2-fix-rc6-small.json`): no offscreen, clipped-v or contrast flag and no console error on the two screens; the flow completes. The flow is not `keyboard: true` as a whole: its first step is a mouse click (the line group is focused programmatically, which draws no ring by design), so only the Enter and `c` steps are checked as keyboard steps.

`node sweep/wave.mjs all` against a baseline shot on origin/main (24b8e7797) in a clean worktree: no newly flagged cells; 10 changed, of which the four new cells are not counted against a baseline. Not covered by a cell: the phone's Raise budget sheet copy (text only) and the MCP version (not UI).
