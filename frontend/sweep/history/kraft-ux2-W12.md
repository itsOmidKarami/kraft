# kraft-ux2-W12: the Library area under /ng

Receipts for the W12 sweep. A new file, not an entry in HISTORY.md (the controller's ruling; W15 adds the README line).

## Baseline

- Taken on `origin/main` at `ed72b06cc` (includes W11 #372 and W16 #369), in a clean detached worktree built from that sha, with `SWEEP_PORT=4397 node sweep/wave.mjs all --baseline`.
- 1663 cells, 242 flagged. The earlier baseline (before main moved) was discarded: the all-sweep is rebaselined on main.
- The branch was rebased on the same `ed72b06cc`, so the two runs differ only by this PR.
- Main then moved to `a816693cf` (W14 A #373 and a CI change #370). Neither touches `frontend/`, so the sweep was not re-run; the branch is rebased onto it before the push.

## Runs

Port: **4397** for the baseline and for both runs. One official sweep at a time.

| run | command | result |
|---|---|---|
| wave | `SWEEP_PORT=4397 node sweep/wave.mjs ux2-W12` | 325 shots, 5 flagged; 6/6 rules pass; 0 regressions |
| all | `SWEEP_PORT=4397 node sweep/wave.mjs all` | 1712 shots, 242 flagged; 1/1 rules pass; 0 newly flagged, 0 cleared |

`1712 - 1663 = 49` new cells; 1655 cells are identical to the baseline; 57 changed = 49 new + 8 existing.

Wave rules (waves.json `ux2-W12`): contrast = 0, offscreen = 0, clipped-v = 0, no console errors on `^ng-`, and flow steps complete on `^flow-ng-(move-to-library|library-)`.

Flow rows are in the manifest: `flow-ng-move-to-library`, `flow-ng-library-blocked-publish`, `flow-ng-library-steering-preview`, `flow-ng-library-rename` (and the other `flow-ng-*` rows from earlier waves, 23 in all).

## New cells (49)

- `ng-library/{list,list-blocked,task,task-config,node,gate,step,task-glyph,steering,review,review-clean}` at 1280, plus 1024 / 1920 and light where the cell asks for them.
- `ng-chains/move-to-library@1280`.
- The four flows above, 18 steps at 1280.
- No 390 cells: /ng has no phone layout until W17.
- Mocks: `MockOptions.ngLibrary` (`clean | draft | blocked`) serves the library draft (add_component, rename, remove, set_field) and `move_to_library` in the chains ops. Nothing is mocked in the product; the flag is off for every shipped cell.

## Existing cells that changed (8), all expected

All `ng-chains/*`: `bottom-empty@1280`, `bottom-handler@1280`, `change-base@1280`, `node@1280`, `node@1280~light`, `node@1920`, `node@1920~light`, `task-menu@1280`.

Pixel difference 0.40% to 0.93%, all inside the chain pane. The cause is W12's `LibraryHint` ("In the library: nodes.verification", a link to the library component the chain node extends) and the Move to library button in the pane's footer. Looked at before/after: the pane content moves down by one row, nothing else changes. No shipped (non-/ng) cell changed.

The earlier baseline showed two flaky cells (`flow-log-maximize/03`, `flow-pause-steer-resume/04`); on this run neither changed.

## Other receipts

- `just test-ui`: 1842 passed after the rebase; vitest output grepped for Unhandled / TypeError: none. `tsc` clean.
- Every new pin was mutated and the same test failed.
- `css.collision.test.ts` passes (the `ng/library/` folder uses `lib-` classes only).
- R10: `.lib-name` has a `data-allow-ellipsis` entry and a `checks.spec.ts` case; README allowlist entry 11.

## Process notes

- The first sweep attempt failed to start its web server: `npm run build` runs `tsc -b`, and a stale tsc cache in `node_modules/.tmp` raised TS2883 in `src/views/settings/testing.tsx`, a file this PR does not touch. Moving the cache aside fixed it; the source was fine.
- The baseline was taken in a separate detached worktree so it never saw this branch's code.
