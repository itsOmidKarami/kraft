# ux2-fix-steer-hide · the paused card hides the steer when not steerable (Kraft-9d8b2.28)

Branch `kraft/ux2-fix-steer-hide`, on `1a3fa5802`. **Sweep port: 4394.**

Baseline: `node sweep/wave.mjs ux2-W5 --baseline` before the cell existed.

## `node sweep/wave.mjs ux2-W5` and `ux2-fix-steer-hide`

ux2-W5: 7/7 rules pass · 1 changed (the new cell) · 0 cleared · 0 regressions. ux2-fix-steer-hide: 5/5 rules pass · 0 regressions (its 101 "changed" cells are unbaselined; the wave's screen is `ng-item`).

New cell: `ng-item/paused-not-steerable@1280`: the paused card with `steerable: false` shows the title and a single primary Resume, no Steer box, no target picker, no "Resume with steer". The mock's items stay steerable, so no existing cell moved.
