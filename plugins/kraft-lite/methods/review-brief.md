---
name: review-brief
description: "Writes the review brief a human reads at the final review gate before merge: what changed, what CI said, what the local review flagged, what was left out, what is uncertain. Use for the task that produces the review brief, which Kraft runs once the merge request is open and before that gate."
---

# The human review brief

This is the last gate before merge. A human reads what you write and decides
whether the change goes in. They have not watched the chain run, they have not
read the pipeline, and they will not go looking — whatever is not in this brief
is not in their decision.

## Before you write

Read, in this order:

- The chain's events for this work item: `kraft view events --json` (the
  worktree you are in names the work item). Ask for JSON: the plain view cuts
  every payload short. `mr_opened` records the merge request that was opened.
  After it, `external_wait_ended` carries each wait's `outcome` and `result`,
  CI's included, and `ci_not_configured` says there was no CI to wait for;
  `kraft view pipelines`, then `kraft view jobs --pipeline P`, give the job
  names. `findings_measured` is one round of the local tests and review: a
  review named in `unmeasured_tasks` did not run, and one with `dropped` wrote
  its findings wrongly, so neither found nothing. `scope_skipped` and
  `node_skipped` are a person skipping a check. If `kraft` refuses the verb or
  cannot be run (a sandboxed worker with no network cannot reach Kraft at
  all), work from what your instructions carry and say in the brief which
  events you could not read.
- Whether a merge train or merge queue rejected this merge request since the
  reviewer last approved it: `kraft view events --type merge_queue_dropped
  --json`, against the newest `gate_approved` whose `gate` is `final_review`
  (`kraft view events --type gate_approved --json`). Each event carries a
  `seq`; a drop with a higher `seq` than that approval, or any drop when there
  is no such approval, is one the reviewer has not seen. For each, read its
  payload (`cause`, `reason`, `failed_jobs`, `url`, and `head_sha`, the head
  Kraft's own rebase left after the rejection) and what the repair committed after it
  (`git log -p <head_sha>..HEAD`).
- The local review findings, including the ones ruled minor.
- The diff itself. A brief written from the spec rather than from the diff
  describes the change that was planned, not the one that was made.

## What the brief contains

In this order, leaving out a conditional part when its condition doesn't hold:

1. If the pipeline is red, a first line that says so, with the failed jobs.
2. A merge train or queue rejection the reviewer hasn't seen.
3. What changed, what CI said and what the local review flagged: always there.
4. Review-bot feedback not acted on, what was left out and what you are unsure
   about, each only when there is something to say.

- **Merge train/queue rejection**, as its own section, whenever a drop
  is newer than the last `final_review` approval. The reviewer approved this
  change once, it went to merge, and it came back: they need to know that
  before anything else. Give the cause, the reason, the failed jobs, and what
  the repair commits changed, so they can review that change rather than
  re-read the whole one. A `conflict` drop that Kraft's own rebase cleared has
  no repair commit; say the branch was rebased onto the target and nothing
  else changed.
- **What changed**, in the terms of this repo: files, functions, behaviour. Not
  a restatement of the work item's title or description.
- **What CI said**, plainly, including the job names. If the pipeline is red,
  say so in the first line — a brief that buries a failed pipeline is worse
  than no brief, because it converts a human check into a rubber stamp.
- **What the local review flagged**, including findings that did not burn a fix
  cycle. A minor finding nobody is shown is a silent discard. A review that
  didn't run, or whose findings were dropped, is reported as that, never as
  clean.
- **Review-bot feedback not acted on**, when your instructions point you at an
  agent's verdict on the automated reviewers' comments. A comment it declined
  was judged wrong, already handled, or not for this change, by an agent and not
  a person: name each one, who made it, where, and why it was declined, from the
  summary your instructions name. Say that it is still unresolved on the merge
  request, because a repository that requires resolved conversations holds the
  merge until a person resolves it. Leave the section out when there is no
  verdict or it declined nothing.
- **What was deliberately left out**, and why, when a plan task or something
  the spec asks for has no change behind it. Scope the reviewer did not expect
  is most of what gets rejected at this gate.
- **What you are unsure about**, when there is something you could not read or
  check, or a task raised a concern about its own work. Doubts are information
  for the human, not an admission. Name them.

## What not to do

Do not argue for approval. You are not selling the change; you are giving the
person who owns the decision what they need to make it. A brief that reads as
advocacy makes the reviewer's job harder, because they have to discount it
before they can use it.

Do not pad a small change into a long page. Three lines is a fine brief for a
three-line diff.
