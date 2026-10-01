# kraft/ux2-fix-hn-lanes

Kraft-9d8b2.17 (R74): the Harnesses lanes canvas no longer runs under the docked pane.

Cause: `.hn-canvas` spanned the whole stage (`inset: 0`, right edge at x=1280 at 1280 wide) and only padded its right edge by the pane's width, so it ran 380px behind the docked pane, where no scroll reaches: at scroll 0 the lane's right end (the 5th task, the `opencode` lane's right text) sat under the pane. The sweep's `offscreen` rule exempts what a scroll strip reaches, so the W14 rule never saw it.

Fix (Decisions §11 holds: a fixed 520px lane, the canvas scrolls): the canvas ends where the pane begins (`right: reserve(open)`, as the Chains canvas reserves it; x=900 at 1280), so scrolling it to the end shows every lane whole, left of the pane. A lane's head wraps if it must, without changing the lane's width.

Rule: `flow-ng-harness-lanes-fit-the-pane` (`cases/ux2-fix-hn-lanes.flows.ts`, `flow-completes` in `waves/ux2-fix-hn-lanes.json`) asserts, on the floor, a harness and a profile at 1280, that the canvas's right edge is at or left of the pane's left edge, and that scrolled to its end every lane's right edge is inside the visible canvas and every part of a lane (head, glyph, "+N more") is inside the lane. On origin/main's code all three steps fail on the first assertion; on this branch they pass. A flow, not a generic rule, because the offscreen check's scroll-strip exemption can't see this. Cell `ng-harnesses-scrolled/floor@1280~light`: the canvas scrolled to its end.

There is no visible before/after at scroll 0: the lane is cut at the pane edge either way (headless Chrome draws no scrollbar), so the evidence is the geometry above.

`node sweep/wave.mjs all` against a baseline shot on origin/main (736eb5799, e2e-shots deleted first): no newly flagged cells; the sweep counts 6 changed.
