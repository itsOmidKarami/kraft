---
name: mr-metadata-repair
description: "Repairs a merge request's metadata, such as a missing or wrong label, so its checks can pass. Kraft runs it as the on_failure repair of the merge request's CI wait, when the failed job's trace names a label."
---

# Repairing a merge request's metadata

The merge request's CI wait just failed. Its diagnosis — the failed job's name
and the tail of its trace — is already in the prompt above.

**Whether another pass follows is the chain's decision, not this skill's.** Fix
what you can see and exit; the CI wait runs again after you and decides whether
it worked. Do not apply a second guess in the same pass to hedge the first.

## When the trace names a missing or wrong label

A job that refuses to run without a label of a given kind usually names the
kind and the choices in its own message (for example: `no type label;
expected one of feature, fix, docs`). If it does not, the project's
CONTRIBUTING or the job's own definition in its CI config says what they
are. Pick the label from the diff this item made, not from habit — a wrong
label mislabels what ships (a release label picks the version), which is
worse than stopping to ask. If the diff does not make the choice obvious,
leave it: reporting "I could not tell which of these labels this change
takes" is correct output; a guess is not.

Apply your choice with the `set_mr_labels` tool of the `kraft` MCP server
(`kraft item mr-label <label>...` from the shell works the same way) — not
`glab`/`gh` directly. It re-creates the pipeline too: `CI_MERGE_REQUEST_LABELS`
is fixed when a pipeline starts, so a label applied after the fact needs a new
pipeline to be seen at all, and this is what makes that happen.

## When the trace names something else

This task exists for MR-metadata problems, not code problems — a missing
label, not a failing test. If the pipeline is red for a reason no label can
fix, do nothing: the CI wait runs again after you, fails the same way, and once
the chain's repair attempts are spent the item stops for a human with the same
diagnosis you would only be repeating.

## When the pipeline reports a merge conflict

The CI wait already asks the forge itself whether this branch can merge and
puts the answer in its own log line (`merge request is not mergeable:
conflict`, or similar) — you do not need to re-derive that by hand. Do not
check out another branch or attempt a local `git merge` to look for
conflicts yourself: this worktree is the item's own, the next task after you
resolves the merge request from whatever branch is checked out, and a merge
left mid-conflict when your pass ends strands it there. If you need more than
the log line gives you, use a read-only command that does
not change what is checked out (`git merge-tree`, `git log`, `glab mr
diff`) — and in any case there is nothing this task can do about a real
conflict; say so and stop, the same as any other non-label failure.
