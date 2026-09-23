---
name: work-brief
description: "Use when the pre-draft gate needs a page for the human."
---

# The work brief

The work is implemented, tested and reviewed locally, and nothing has left this
machine yet. A human reads what you write and decides whether it should. They
are answering one question: **should this become a draft merge request?** They
have not watched the chain run and will not go looking. Whatever is not in this
brief is not part of their decision.

## Before you write

Read these first:

- The work item's title and description, and the spec and plan if it has them.
  They are in your instructions above, or named there by path.
- What the chain did: `kraft view events` (the worktree you are in names the
  work item). The ones that matter are `findings_measured` (what the tests and
  the review found, each round), `fix_cycle_started` and `judge_verdict` (the
  repair attempts, and what the fix-loop judge said about them), and
  `node_recovery_started`.
- The size of the change: `kraft view diff --stat`. Read files where you need
  to, but do not paste the diff.

## What the brief contains

Use these sections, in this order. A section with nothing in it says so in one
line; do not drop it.

1. **What was asked.** The title, plus the spec and plan by path. Do not
   restate them.
2. **What changed.** The files and the scale (files touched, lines added and
   removed), and in a sentence or two what the change does. Not the diff.
3. **What was verified.** Which test scopes ran and what they reported on the
   last round. If a round was red and a repair turned it green, say so.
4. **What the review found, and what was done about it.** Each finding the
   local review raised, and whether a repair fixed it or it was left open. A
   minor finding that never opened a fix cycle still goes here: a finding
   nobody is shown has been silently discarded.
5. **What is unresolved.** How many repair attempts were spent out of how
   many, what the judge said, and anything knowingly skipped or left for
   later. A doubt you have is information for the human, not an admission.
6. **What approving does.** Say it plainly: approving **opens a draft merge
   request on the forge and starts CI**. The work is then published, even
   though it is still a draft. A human who does not know that may approve
   casually.

If your instructions name an intent tree, **What changed** also lists each
requirement this diff adds, changes or removes, by id, and any requirement it
leaves with no `enforced-by:` pin.

## What it leaves out

- **The diff.** The human can read it themselves, and the brief is where they
  decide whether they need to.
- **Anything the final review brief will cover.** The merge request, what CI
  said about it and the automated review come later, at the last gate, in the
  review brief. This page is about the local work only.
- **Advocacy.** You are not selling the change. Give the human what they need
  to decide, and leave the decision to them.

Keep it short. A small change gets a short brief.
