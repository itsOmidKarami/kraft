---
name: merge-queue-repair
description: "Repairs what made a merge train (GitLab) or merge queue (GitHub) reject a merge request that had passed its own pipeline: reproduces the failure against the target branch as it is now and fixes the code. Kraft runs it as the on_merge_rejected repair of the merge node, after the forge dropped the merge request from its train or queue; Kraft then sends the item back through the merge request's checks and its final review."
---

# Repairing a merge train or merge queue rejection

The merge request passed its own pipeline, was approved and was queued. The
merge train or merge queue then ran its own pipeline on it, merged into the
target branch as it is now (and on GitLab, after the merge requests ahead of
it in the train), and that pipeline failed. Why, which pipeline and which jobs
are already in the prompt above.

So the failure comes from one or both of:

- **The target branch as it is now.** Something landed on it after this
  branch was tested: a renamed function, a changed fixture, a new lint rule, a
  test that now collides with yours. Your branch is fine against the base it
  was tested on and broken against the one it would land on.
- **Jobs that run only on the train or queue.** Some projects run a heavier
  suite, or a different one, only for the merge pipeline. This branch's own
  pipeline never ran them, so they are seeing this change for the first time.

## Find out which

If the prompt tells you to rebase this branch first, do that before anything
else: the rest of this method assumes the branch already sits on the target.

Read the failed jobs the prompt names, then their logs: open the pipeline from
the prompt's link with the forge's own read-only CLI (`glab ci view`, `glab ci
trace <job>`, `gh run view --log-failed`) or its web page. A log names the
failing test or check far more precisely than a job name does; read it before
you change anything.

Then reproduce it where you can. Fetch the target branch and run the failing
job's command on this branch combined with `origin/<base>`, the way the train
or queue did: in a scratch worktree (`git worktree add`) of a merge of the two,
never by merging or checking out another branch in this one. A failure you
reproduced is a failure you can confirm you fixed. One you could not reproduce
(a job that needs a service or a secret only the forge has) you fix from the
log alone; say so in your report.

## Fix the code, not the pipeline

The train's or queue's jobs are the project's own definition of what may land.
Fix the code so it passes them: adapt to what changed on the target branch,
fix the test that collides, satisfy the check that only runs there. Do not
edit the CI configuration to skip, relax or remove a job, and do not mark a
test as skipped or expected to fail to get past it.

The one exception is a train-only or queue-only job that is itself wrong: it
fails on the target branch alone, with no change of yours in it, or it tests
something this project no longer has. That is not yours to fix by editing it
and not something a code change can satisfy. Change nothing and report
`needs_context`, naming the job and what you saw, so a person decides.

A failure that is not this change's at all (a flaky job, a runner that ran out
of disk, a registry that timed out) is also not something to patch around.
Report it plainly, with the log line that shows it.

## Finish

Commit your fix on this branch. A repair that commits nothing stops the item
for a person, since the train would only reject the same head again; that is
the right outcome when there is nothing in the code to fix.

Never push, never merge, and never put the merge request back on the train or
in the queue yourself. Kraft does all three: after your commit it sends the
item back through the merge request's own checks and its final review, and only
then queues it again.
