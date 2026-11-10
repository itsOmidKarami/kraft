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

- The chain's events for this work item: `kraft view events` (the worktree you
  are in names the work item). `mr_opened` records the merge request that was
  opened; the events after it say what the pipeline said about it. If CI was
  not configured or never ran, the brief says that instead. If `kraft` refuses
  the verb or cannot be run (a sandboxed worker with no network cannot reach
  Kraft at all), work from what your instructions carry and say in the brief
  which events you could not read.
- Whether a merge train or merge queue rejected this merge request since the
  reviewer last approved it: `kraft view events --type merge_queue_dropped
  --json`, against the newest `gate_approved` whose `gate` is `final_review`
  (`kraft view events --type gate_approved --json`). Each event carries a
  `seq`; a drop with a higher `seq` than that approval, or any drop when there
  is no such approval, is one the reviewer has not seen. For each, read its
  payload (`cause`, `reason`, `failed_jobs`, `url`, and `head_sha`, the head
  the train or queue rejected) and what the repair committed after it
  (`git log -p <head_sha>..HEAD`).
- The local review findings, including the ones ruled minor.
- The diff itself. A brief written from the spec rather than from the diff
  describes the change that was planned, not the one that was made.

## What the brief contains

- **Merge train/queue rejection**, at the top, as its own section, whenever a drop
  is newer than the last `final_review` approval. The reviewer approved this
  change once, it went to merge, and it came back: they need to know that
  before anything else. Give the cause, the reason, the failed jobs, and what
  the repair commits changed, so they can review that change rather than
  re-read the whole one. A `conflict` drop that Kraft's own rebase cleared has
  no repair commit; say the branch was rebased onto the target and nothing
  else changed. Leave the section out when there is no such drop. A red
  pipeline still goes in the first line, ahead of this section: the two
  never compete, because one line saying CI is red comes first, then this
  section, then the rest.
- **What changed**, in the terms of this repo: files, functions, behaviour. Not
  a restatement of the work item's title or description.
- **What CI said**, plainly, including the job names. If the pipeline is red,
  say so in the first line — a brief that buries a failed pipeline is worse
  than no brief, because it converts a human check into a rubber stamp.
- **What the local review flagged**, including findings that did not burn a fix
  cycle. A minor finding nobody is shown is a silent discard.
- **Review-bot feedback not acted on**, when your instructions point you at an
  agent's verdict on the automated reviewers' comments. A comment it declined
  was judged wrong, already handled, or not for this change, by an agent and not
  a person: name each one, who made it, where, and why it was declined, from the
  summary your instructions name. Say that it is still unresolved on the merge
  request, because a repository that requires resolved conversations holds the
  merge until a person resolves it. Leave the section out when there is no
  verdict or it declined nothing.
- **What was deliberately left out**, and why. Scope the reviewer did not
  expect is most of what gets rejected at this gate.
- **What you are unsure about.** Doubts are information for the human, not an
  admission. Name them.

## What not to do

Do not argue for approval. You are not selling the change; you are giving the
person who owns the decision what they need to make it. A brief that reads as
advocacy makes the reviewer's job harder, because they have to discount it
before they can use it.

Do not pad a small change into a long page. Three lines is a fine brief for a
three-line diff.
