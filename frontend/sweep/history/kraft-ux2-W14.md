# ux2-W14 · Harnesses

Branch `ux2/W14` pushed as `kraft/ux2-W14`, on `8105f2e28` (W15 #375). Backend half: #373 (merged, `a816693cf`).

**Sweep port: 4392** for the baseline and both end runs. The baseline is `node sweep/wave.mjs all --baseline` shot from a clean detached checkout of `8105f2e28` (1784 cells), copied to `e2e-shots/baseline/all`, so nothing of this branch is in it.

## `node sweep/wave.mjs ux2-W14`

6/6 rules pass · 35 cells in scope, all new (no baseline: the screen did not exist) · 0 regressions.

| Rule | Result |
|---|---|
| contrast = 0 on `^ng-harnesses` | pass |
| offscreen = 0 on `^ng-harnesses` | pass |
| clipped-v = 0 on `^ng-harnesses` | pass |
| no console errors on `^ng-harnesses` | pass |
| flow steps complete on `^flow-ng-` | pass |
| no newly flagged cells | pass |

New cells: `ng-harnesses/{floor, harness, harness-lane, profile, entry, floor-config, problems, problems-profile, review, review-problems, yaml, empty}` and the flow `ng-harness-never-blocks-publish` (set Never, the problem names the task, Publish blocked, set back, a real publish).

## `node sweep/wave.mjs all`

1/1 rule passes · 1819 cells · 35 new · 2 existing cells changed · 0 newly flagged, 0 cleared.

The two changed are shipped or other-wave flows that move between any two runs of `main` and are not touched by this branch: `flow-log-maximize/03-wheel-up-pauses-follow@1280` (the same one W15 recorded) and `flow-ng-apply-restart/05-back-and-cleared@1280`, whose baseline PNG is a blank white page caught mid-reload while this run's shows the board. No other existing cell changed.

## Real server (done before G moved the page onto the shared frame)

A dev server (`KRAFT_DEV_PORT=8786 just api`): the resolve answer matches the page's types; claude lists 14 tasks; setting claude to Never gives three problems carrying `chain` and `path`, plus the escalation one. Stopped by its pid.

## Unit tests

`just test-ui`: 211 files, 1991 tests, no Unhandled or TypeError. New pins were each mutated and failed their own test.
