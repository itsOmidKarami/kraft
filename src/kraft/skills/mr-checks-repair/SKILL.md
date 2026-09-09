---
name: mr-checks-repair
description: Read why a merge request's pipeline just failed, and repair what is fixable from here — starting with a missing label.
---

# Repairing a red mr_checks pipeline

`on.ci.poll` just failed. Its diagnosis — the failed job's name and the tail
of its trace — is in that task's own worker log, in this same round; read it
before doing anything else. You get one pass: `on.ci.poll` re-runs after you
exit and decides whether it worked, so there is no point guessing twice.

## When the trace names a missing or wrong label

A job that refuses to run without a label of a given kind names the kind and
the choices in its own message (for example: `no release:: label; expected
one of ('major', 'minor', 'patch', 'none')`). Pick the label from the diff
this item made, not from habit — a wrong guess ships a wrong version, which
is worse than stopping to ask. If the diff does not make the choice obvious,
leave it: reporting "I could not tell if this is a major or minor change" is
correct output; a guess is not.

Apply your choice with the `set_mr_labels` tool (`kraft item mr-label
<label>...` from the shell works the same way) — not `glab`/`gh` directly. It
re-creates the pipeline too: `CI_MERGE_REQUEST_LABELS` is fixed when a
pipeline starts, so a label applied after the fact needs a new pipeline to be
seen at all, and this is what makes that happen.

## When the trace names something else

This task exists for MR-metadata problems, not code problems — a missing
label, not a failing test. If the pipeline is red for a reason no label can
fix, do nothing: `on.ci.poll` runs again right after you, fails the same way,
and the item stops for a human with the same diagnosis you would only be
repeating.
