---
name: mr-metadata-repair
description: "Repairs a merge request's metadata, such as a missing or wrong label or a missing description section, so its checks can pass. Kraft runs it as the on_failure repair of the merge request's CI wait, on every failure; it acts only when the failed job's trace names a label or a description section."
---

# Repairing a merge request's metadata

The merge request's CI wait just failed. Its diagnosis — the failed job's name
and the tail of its trace — is already in the prompt above.

**Whether another pass follows is the chain's decision, not this skill's.** Fix
what you can see and exit; the CI wait runs again after you and decides whether
it worked. Do not apply a second guess in the same pass to hedge the first,
and never push a commit or touch CI config to force a run.

Act only when the trace names a label or a description section. When you
leave something, whatever the reason, finish `done_with_concerns` and say why
in `concerns`.

## When the trace names a missing or wrong label

A job that refuses to run without a label of a given kind usually names the
kind and the choices in its own message (for example: `no type label;
expected one of feature, fix, docs`). If it does not, the project's
CONTRIBUTING or the job's own definition in its CI config says what they
are. Pick the label from the diff this item made, not from habit — a wrong
label mislabels what ships (a release label picks the version), which is
worse than stopping to ask. If the diff does not make the choice obvious,
leave it: "I could not tell which of these labels this change takes" in
`concerns` is correct output; a guess is not.

Apply your choice with the `set_mr_labels` tool of the `kraft` MCP server
(`kraft item mr-label <label>...` from the shell works the same way) — not
`glab`/`gh` directly. It adds labels, and removes only those sharing a scope
prefix with one it sets (`kind::a` gives way to `kind::b`); it can't remove an
unscoped label, so if a stray one fails the check, say so. On GitLab it also
starts a new pipeline, because `CI_MERGE_REQUEST_LABELS` is fixed when a
pipeline starts; on GitHub the label edit re-runs workflows that trigger on
`labeled`.

## When the trace names a missing or wrong description section

A job that reads the merge request's description (for example `missing
section: ## Testing`) is fixed in the item's `mr_meta` document, whose
path is in the documents list above. Edit that file, not the live description:
Kraft rebuilds the description from it on every sync, so an edit anywhere else
is reverted. Keep its front matter, change only the body, and take the
section's format from CONTRIBUTING, the PR template or the job's own checker
(run it on the new body if the repo ships one). Fill it from the diff, never
from guesswork; if you cannot tell what it should say, leave it. The `sync`
step after you writes the description to the forge and starts the failed
checks again (on GitLab as a new pipeline). A check that reads the description
when it runs sees the fix; one that reads it from its triggering event does
only if it re-runs on edits.

## When the trace names something else

This task exists for MR-metadata problems, not code problems — a missing
label, not a failing test. If no label or section would fix it, change nothing,
end `done_with_concerns` and name the failed job and the cause in `concerns`. A
chain with a fix loop takes it from there.

## When the pipeline reports a merge conflict

The CI wait already asks the forge itself whether this branch can merge and
puts the answer in its own log line (`merge request is not mergeable:
conflict`, or similar) — you do not need to re-derive that by hand. Do not
check out another branch or attempt a local `git merge` to look for
conflicts yourself: this worktree is the item's own, the next task after you
resolves the merge request from whatever branch is checked out, and a merge
left mid-conflict when your pass ends strands it there. If you need more than
the log line gives you, use a read-only command that does not change
what is checked out (`git merge-tree`, `git log`, `git diff
origin/<base>...HEAD`) — and in any case there is nothing this task can
do about a real conflict; say so in `concerns` and stop, the same as any
other non-label failure.
