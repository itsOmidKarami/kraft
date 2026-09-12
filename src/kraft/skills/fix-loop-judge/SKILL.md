---
name: fix-loop-judge
description: Judge whether a fix loop's next cycle is worth spending, from the trend across the rounds already run.
---

# Judging a fix loop mid-run

You are not reviewing code and you are not fixing anything. A fix loop
(`verify`, `mr_checks`, or any other node with a `fix_loop`) has already run
at least one cycle; before it spends another, you are asked whether that is
still a good trade.

Your task instruction already carries everything you are entitled to judge
from: every round's eligible findings (with a fingerprint stable across
rounds -- the same tag reappearing is the same finding, not a new one), and
the budget spent so far against the loop's cap. You do not have, and should
not go looking for, the full diff or the fix agent's reasoning -- if the
history given to you is not enough to decide, say so in `concerns` and
report `continue`; a false stop costs more than one more cycle spent on
something that turns out fine.

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

Write `verdict` into your result file, with your reasoning in `concerns`
(required for every verdict -- it is the only record of why, and for
`stop_downgrade` it is what a human reads at the review gate later):

- `"verdict": "continue"` -- worth another cycle.
- `"verdict": "stop_needs_human"` -- the loop is not converging; a person
  should look rather than pay for another guess.
- `"verdict": "stop_downgrade"` -- the remaining findings are real but not
  worth the cost of chasing further; let the chain proceed with them
  unresolved, for a human to weigh at the review gate.

Prefer `continue` when you are unsure. Stopping early on a loop that would
have converged in one more cycle is the more expensive mistake in both
directions -- it either strands the item for a human who then just retries
it, or lets a real finding through unresolved for a saving that never
materializes.
