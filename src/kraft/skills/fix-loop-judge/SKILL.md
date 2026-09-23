---
name: fix-loop-judge
description: "Use when a fix loop has run at least one cycle and Kraft asks whether another is worth spending."
---

# Judging a fix loop mid-run

You are not reviewing code and you are not fixing anything. A fix loop
(`verify`, `mr_checks`, or any other node with a `fix_loop`) has already run
at least one cycle; before it spends another, you are asked whether that is
still a good trade.

Your task instruction already carries what you judge from: every round's
eligible findings (with a fingerprint stable across rounds -- the same tag
reappearing is the same finding, not a new one), and the budget spent so far
against the loop's cap. When your task declares the review package,
`$KRAFT_REVIEW_PACKAGE` names a file with the change itself -- the whole
branch the first time you are asked, then only what changed since your last
session. Read it to see whether the code behind a recurring finding actually
moved, not to review the change again. Do not go looking for the fix agent's
reasoning; it is not evidence about whether the code moved.

## What to weigh

- **Shrinking vs recurring vs growing.** Fewer findings each round, even
  slowly, is progress worth continuing. The same fingerprint surviving two
  or more rounds after a fix cycle specifically targeted it is not progress
  -- it is a fix agent that cannot land the change, and another cycle is
  very unlikely to differ. New findings appearing as fast as old ones clear
  is the same story by a different route.
- **Severity of what is actually still open**, not what has already been
  fixed. A loop that cleared every `critical` finding and has one
  `important` naming nit left recurring is a different call than one still
  chasing a `critical` security hole.
- **Cost already spent vs cap remaining.** A loop three cycles into a
  two-attempt-remaining budget that is still finding new things each round
  is close to where the cap would stop it anyway -- `stop_needs_human` there
  costs nothing the cap would not have cost a cycle later, and saves the
  wasted cycle.

## Reporting your decision

Report the verdict, and the reasoning in `concerns`, exactly as your task
instruction lays out. `concerns` is required for every verdict: it is the only
record of why, and for `stop_downgrade` it is what a human reads at the review
gate later.

When you are unsure, report `continue`, and say what you could not tell in
`concerns`. A loop stopped early that would have converged in one more cycle
either strands the item for a human who then just retries it, or lets a real
finding through unresolved for a saving that never comes.
