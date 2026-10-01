# kraft/ux2-fix-set-harness · Kraft-9d8b2.15

The /ng Harnesses pane edits a harness's own fields (`set_harness`). Sweep port 4393. Baseline: `node sweep/wave.mjs all --baseline` shot from a clean detached checkout of `f07344fd6` (1818 cells), copied into this worktree.

## `node sweep/wave.mjs ux2-fix-set-harness`

5/5 rules pass (contrast, offscreen, clipped-v, console on `^ng-harnesses`; no newly flagged). One new cell: `ng-harnesses/harness-edited` (claude-sandbox disabled and effort `max`, so its fields draw amber), in `sweep/cases/ux2-fix-set-harness.ts`.

## `node sweep/wave.mjs all`

1/1 rule passes · 1820 cells (2 new: `harness-edited@1280` and its `~light`) · 13 changed · 0 newly flagged.

Changed, all expected: `ng-harnesses/harness@1280` (+`~light`) and the seven steps of `flow-ng-harness-never-blocks-publish` (the claude pane now shows controls instead of read-only rows), the two new cells, and `flow-log-maximize/03-wheel-up-pauses-follow@390/@1280`, which moves between any two runs of main and is not touched here.

An earlier `all` run flagged `ng-chains/node-empty@1280` with a 5 s/8 s `locator.waitFor` setup timeout while other sessions' sweeps loaded the machine. Alone, on this branch, it records no `setupError` (865 ms); the last `all` run is clean.
