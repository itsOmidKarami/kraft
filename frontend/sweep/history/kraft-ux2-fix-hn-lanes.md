# kraft/ux2-fix-hn-lanes

Kraft-9d8b2.17 (R74): the Harnesses lanes no longer run under the docked pane.

Cause: `.hn-canvas` spanned the whole stage (`inset: 0`) and only padded its right edge by the pane's width, while each lane is a fixed 520px. At 1280 the canvas shows ~400px beside the pane, so the lane's right end (the 5th task, the `opencode` lane's right text) sat under the pane at scroll 0, reachable only by scrolling sideways. The sweep's `offscreen` rule exempts what a scroll strip reaches, so the W14 wave rule never saw it.

Fix: the canvas ends where the pane begins (`right: reserve(open)`, as the Chains canvas reserves it), a lane is `min(520px, 100%)` (520px when there is room), and a lane's head wraps instead of overflowing. The "lanes are 520px wide" pin now reads the `min()`.

Rule: `flow-ng-harness-lanes-fit-the-pane` (`cases/ux2-fix-hn-lanes.flows.ts`, rule `flow-completes` in `waves/ux2-fix-hn-lanes.json`) asserts on the floor, a harness and a profile at 1280, with the pane docked: no sideways scroll in the canvas, every lane's right edge at or left of the pane's left edge, and every part of a lane (head, glyph, "+N more") inside the lane. On the code before the fix all three steps fail on those assertions; after, they pass.

`node sweep/wave.mjs all` against a baseline shot on origin/main (736eb5799, e2e-shots deleted first): no newly flagged cells; the sweep counts 21 changed, of which the `ng-harnesses` cells are the intended ones. Before / after of `ng-harnesses/floor@1280~light`: `kraft-ux2-fix-hn-lanes-floor.png` (left main, right this branch).
