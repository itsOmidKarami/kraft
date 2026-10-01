# ux2-W18 PR 1 · cutover groundwork (A–E)

Branch `kraft/ux2-W18`, on `feb6b6086`. **Sweep port: 4392.**

Baseline: `node sweep/wave.mjs all --baseline` in a detached worktree on `feb6b6086` (origin/main), copied to this worktree's `e2e-shots/baseline/all` before `all` ran here.

## `node sweep/wave.mjs all`

1/1 rules pass · 0 changed · 2 cleared · 0 regressions · 1818 cells.

PR 1 changes no screen: the shipped-address aliases (B) answer only addresses no cell visits, the contract case (C) is a test, and the composer picker's search kind (D fix) is not visible in a mocked cell. The two cleared cells, `ng-chains/gate@1280` and `ng-chains/node-empty@1280`, moved 0% in pixels; their flags come and go between runs of main itself, and nothing in this PR touches the Chains page.

No cell, flow or rule is added, so there is no `cases/ux2-W18.ts` or `waves/ux2-W18.json` yet; PR 2 adds both.

## Playwright

`just e2e-ci`: 48 tests, 48 passed, 1.7 min locally, one worker. The 19 in `e2e/v2/` are new; the shipped specs run unchanged beside them on the same server.
