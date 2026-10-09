---
name: work-brief
description: "Writes the work brief a human reads at the pre-draft gate to decide whether the locally finished work becomes a draft merge request. Use when a chain task produces the work_brief, after implementation, tests and local review and before any merge request is opened."
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
- What the chain did: `kraft view events --json` (the worktree you are in names
  the work item). Ask for JSON: the plain view cuts every payload short, and
  the facts you need are past the cut. These events matter:

  | Event | What it tells you |
  |---|---|
  | `findings_measured` | One round of the tests and the review. `measured_tasks` are the checks that ran in it and `unmeasured_tasks` the ones that did not: a step that fails stops the steps after it, so a round of red tests has no review in it. A test command that failed is named in a finding, so a round with no test finding passed every command it selected. `dropped` is there only when a reviewer wrote findings wrongly, and that review is not a clean one. |
  | `test_scopes_selected` | The test commands the round ran. None selected means nothing was tested. |
  | `fix_cycle_started` | A repair attempt. `max_attempts` is how many the loop allows. If `cycle` starts again at 1, a retry restarted the loop: count each run on its own. |
  | `fix_cycle_refunded` | The attempt with that `cycle` did not count. |
  | `judge_verdict` | What the fix-loop judge said about going on. It rules only when a repair left something to fix, so most runs have none. |
  | `scope_skipped`, `node_skipped` | A person skipped a check, whatever the lists above say. A skip from before the last `run_forked` no longer holds, and a `node_skipped` with `gate` set skipped a gate. |
  | `node_recovery_started` | Kraft reran a node's failed tasks. |
  | `worker_session_exited` | With `concerns`, what a task flagged about its own work when it finished. |

  You may not be able to read all of this: `kraft` refuses the verb or cannot
  be run (a sandboxed worker with no network cannot reach Kraft at all), an
  older item's events lack a field named here, or the verification node has no
  `findings_measured`. Then work from what you have and say in the brief what
  you could not read; do not reconstruct it from memory.
- The size of the change: `git diff --stat origin/<base>...HEAD`, where
  `<base>` is the branch the work goes into (the repository's default unless
  the work item names another). Read files where you need to, but do not
  paste the diff.

## What the brief contains

Every brief has these four sections, in this order:

1. **What was asked.** The title, plus the spec and plan by path. Do not
   restate them.
2. **What changed.** The files and the scale (files touched, lines added and
   removed), and in a sentence or two what the change does. You did not write
   the change, so check the plan's tasks against the diff, and say in one
   sentence whether all of them have a change behind them.
3. **What was verified.** From the last round: each check that ran and what it
   reported, and the test commands it ran. If a check did not run in it, name
   that check as not run. Then what it took to get there: the rounds that
   were red before a repair turned them green, and how many repair attempts
   were spent out of how many. If the judge ruled, say what it said.
4. **What approving does.** Say it plainly: approving **opens a draft merge
   request on the forge and starts CI**. The work is then published, even
   though it is still a draft. A human who does not know that may approve
   casually.

Between the third and the last, add a section for each of these that you
found. The human reads a section's presence as "there is something here for
you", so one that reports nothing costs them a read to learn that. When you
found none of them, the brief is the four sections above. A line works the
same way: write what happened, and give no line to what you looked for and
did not find (a judge verdict, a skip, a refund, a concern). The human takes
a brief without one as a run without one.

- If the review raised a finding in any round, add **What the review found,
  and what was done about it.** Each finding, and whether a repair fixed it or
  it was left open. A minor finding that never opened a fix cycle still goes
  here: a finding nobody is shown has been silently discarded.
- If a plan task, or something the spec asks for, has no change behind it,
  add **What was deliberately left out.** Name it, and say why if the work
  says why.
- If there is something you could not read or check, or a task raised a
  concern about its own work, add **What you are unsure about.** Say what,
  and where the human should look themselves. A doubt you have is information
  for the human, not an admission.

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
