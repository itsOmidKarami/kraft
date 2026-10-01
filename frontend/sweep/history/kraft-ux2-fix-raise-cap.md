# kraft/ux2-fix-raise-cap

Kraft-i2tq4 (R73): a cap stop names the limit that stopped it, and /ng raises it.

Rules (`waves/ux2-fix-raise-cap.json`): `ng-item-cap` has no offscreen, clipped-v or contrast flag; no console error except `editor-refused` (the refusal is a 422 the page logs, on purpose, as `ng-search/docs-error` is); `flow-ng-raise-cap-keyboard` completes.

Cells (`cases/ux2-fix-raise-cap.ts`): `ng-item-cap/no-limit` (1280 dark and light, 768: the banner's "Open config"), `limit` (1280 dark and light) and `limit-long` (long data), `editor-attempts` (1280 dark and light, 768, 1024: fix attempts with a maximum), `editor-time` (1280: the work item's time cap, no maximum), `editor-refused` (1280 dark and light: a 422 inline). Flow `ng-raise-cap-keyboard`: Enter on Raise cap opens the editor with the field focused, Save & retry is enabled only above the current value, Esc returns focus to the button, Enter in the field PATCHes then retries. The cells set `stop.limit` on the mock's `capped` scenario in the cell itself, so no existing cell's data changed.

`node sweep/wave.mjs all`, baseline shot on origin/main (8dcab3f79, after #396) with e2e-shots deleted first: no newly flagged cells. The existing capped cells (`ng-item/chain-config-capped-long`, ...) have no `stop.limit`, so their banner now reads "Open config" instead of "Raise cap"; those are among the changed cells. Not covered: the board peek's Banner (the same component, no cell of its own).
