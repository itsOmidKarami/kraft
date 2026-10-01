# ux2-fix-board-step-mr · the board row's step position and merge request (Kraft-gp9h1)

Branch `kraft/ux2-fix-board-step-mr`, on `8280bc3b3`. **Sweep port: 4394.**

Baseline: `node sweep/wave.mjs ux2-W6 --baseline` on this worktree before the fixture changed.

## `node sweep/wave.mjs ux2-W6`

7/7 rules pass · 51 changed · 0 cleared · 0 regressions.

The changed cells are the board and peek cells whose rows now read differently: the running row "2 of 3 · review › code_review" (was "implementation · task 2 of 3"), and every done row "merged !142" (was "completed"). Nothing else moved; no new element is drawn, so there is no new case or rule.
