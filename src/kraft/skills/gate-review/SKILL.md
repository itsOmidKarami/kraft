---
name: gate-review
description: "Reviews the document a gate is about and reports a verdict, so a person only sees the gates that need one. Use as the method of a gate's `auto_review` task, which Kraft runs when the gate opens, before a person is asked."
---

# Gate Review

A gate exists because someone decided this point in the work deserves a person's
attention. You are not here to make gates go away. You are here so that the
gates a person genuinely does not need to see stop reaching them, and so that
the ones they do need to see arrive already read. You report; Kraft decides
what your report means, and the API refuses a worker that tries to approve or
reject the gate itself.

## The default is `undecided`

Not `approve`. A gate wrongly cleared costs a bad spec implemented, a bad plan
followed, or a bad diff merged. A gate wrongly held costs a human one glance at
something you already summarised for them. These are not the same price.

Approve only when you can say concretely why the artifact is right — not merely
that you found nothing wrong with it.

## What each gate is asking

A gate's **id is whatever its chain calls it**, so do not match on names. The
prompt tells you the gate id and the document it is about; the document's *kind*
is what tells you which question below is yours.
- **A gate about a spec** — does the spec solve the problem in the brief, and is
  it a design a person would recognise as theirs? A spec that solves a
  *different* problem, however well, is a reject.
- **A gate about a plan** — do the plan's steps produce the spec's design?
  Missing test steps and hand-wavy steps ("handle errors appropriately") are
  rejects.
- **A gate about a chain revision** — the change set a chain-review agent
  proposed for the nodes still to run, shown with each change's evidence and the
  diff it makes. Does each change follow from the line of the spec or plan it
  cites, and does the chain after it still do the work? A skipped check needs
  stronger evidence than an added one. A proposal the document says cannot be
  applied is a reject: put the reason it gives in your concerns, so the revision
  is rewritten. (A proposal that changes nothing never reaches this gate.)
- **A gate about a review brief or work-item summary** — the final review of
  the finished item and its merge request (`final_review` in the shipped
  chain). Does the finished work match what was approved, and does its size match
  the plan's? This is the one gate whose own document must exist; without it
  there is nothing to finalize, so report `undecided` and say it is missing.
- **A gate with no document at all** — a checkpoint before something
  irreversible starts (opening a merge request, for instance). Read the worktree
  and the diff, and judge whether the work is ready for that step.
- **A gate about a diff that is about to be merged** — the highest bar of any
  gate. When in doubt at all, hand it to a person.

## Reporting

Report your verdict and `concerns` exactly as your task instruction lays out.
Fill `concerns` whatever the verdict: for `undecided` it is what a person reads
first, and for a `reject` it is the steering note, so write it as an
instruction. As an illustration, not wording to copy: "The retry handling is
wrong and the tests are thin" is a complaint; "Task 3 retries on any exception:
retry only on `TimeoutError`, and add the failing case to `test_client.py`" is
an instruction. After a `fixed`, the node re-runs and is measured again, so commit
the fix and do not approve your own edit.
