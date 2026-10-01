# ux2-fix-worktree-gone · an item whose worktree is gone (Kraft-9d8b2.7)

Branch `kraft/ux2-fix-worktree-gone`, on `f07344fd6` (the sweep split, #383). **Sweep port: 4394.**

The cell is `cases/ux2-fix-worktree-gone.ts`, its rules `waves/ux2-fix-worktree-gone.json`.

## `node sweep/wave.mjs ux2-fix-worktree-gone`

5/5 rules pass · 0 regressions. The wave's screen is `ng-item`, so it re-shoots the 100 existing `ng-item` cells with no baseline for this branch: "100 changed" means unbaselined, not changed. The new cell is `ng-item/kebab-no-worktree@1280`: the item menu open on a running item whose detail says `worktree_exists: false`, "Open worktree in editor" greyed with "worktree removed · Retry recreates it" under it. Its four rules (contrast, offscreen, clipped-v, no console errors) pass.
