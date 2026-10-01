# ux2-fix-worktree-gone · an item whose worktree is gone (Kraft-9d8b2.7)

Branch `kraft/ux2-fix-worktree-gone`, on `8280bc3b3`. **Sweep port: 4394.**

Baseline: `node sweep/wave.mjs ux2-W5 --baseline` on this worktree before the cell and the mock flag existed.

## `node sweep/wave.mjs ux2-W5`

7/7 rules pass · 1 changed · 0 cleared · 0 regressions. The one change is the new cell `ng-item/kebab-no-worktree@1280`: the item menu open on a running item whose detail says `worktree_exists: false`, "Open worktree in editor" greyed with "worktree removed · Retry recreates it" under it.

The cell sits in `sweep/sweep.spec.ts` beside `ng-item/kebab`: `sweep/cases/` and `sweep/waves/` are not on main yet (the sweep split), so no per-PR case or rules file.
