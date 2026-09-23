---
name: review-brief
description: "Use when the final review gate needs a page for the human."
---

# The human review brief

This is the last gate before merge. A human reads what you write and decides
whether the change goes in. They have not watched the chain run, they have not
read the pipeline, and they will not go looking — whatever is not in this brief
is not in their decision.

## Before you write

Read, in this order:

- The chain's events for this work item: the merge request that was opened, and
  what the pipeline said about it.
- The local review findings, including the ones ruled minor.
- The diff itself. A brief written from the spec rather than from the diff
  describes the change that was planned, not the one that was made.

## What the brief contains

- **What changed**, in the terms of this repo: files, functions, behaviour. Not
  a restatement of the work item's title or description.
- **What CI said**, plainly, including the job names. If the pipeline is red,
  say so in the first line — a brief that buries a failed pipeline is worse
  than no brief, because it converts a human check into a rubber stamp.
- **What the local review flagged**, including findings that did not burn a fix
  cycle. A minor finding nobody is shown is a silent discard.
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
