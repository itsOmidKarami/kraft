---
name: plan
description: "Writes the implementation plan for a work item from its human-approved spec, as small independently reviewable tasks. Kraft runs it as the library's plan_author task, after the spec gate and before implementation. Use when a chain needs that plan written or revised."
---

# Writing a plan, headless

The spec for this work item is already written and already approved by a human:
your instructions list this work item's documents, or the documents attached at
intake. Read the spec before you do anything else.
Your plan implements that spec and argues from it. If you find yourself
disagreeing with the spec, say so in the plan; do not quietly design something
else.

## Asking

Anything you can settle by reading the code, settle by reading the code. Stop
with `needs_context` only for what the spec leaves genuinely undecidable.

## If you are revising

A human read the existing document and asked for changes; their note leads your
task instruction. Change what they objected to and leave the rest alone, rather
than rewriting the whole thing to look new.

## What the plan contains

A list of tasks, each under a `### Task N: title` heading numbered from 1: Kraft
counts those headings to report progress and to tell the implementer how many
tasks there are. A task is the smallest unit that carries its own test cycle
and is worth a fresh reviewer's gate. Fold setup, configuration and
documentation into the task whose deliverable needs them. Split only where a
reviewer could reject one task and approve its neighbour.

Each task names:

- the exact files it creates, modifies (with line numbers) and tests;
- what it consumes from earlier tasks and what later tasks consume from it —
  exact names, exact types;
- the failing test, written out in full;
- the implementation, written out in full;
- the command that runs the test, and what it prints when it passes.

A shape to follow, with invented names:

```
### Task 2: Reject expired tokens at login
Files: modify `app/auth.py:40-62`; test `tests/test_auth.py`
Consumes: `Token.expires_at: datetime` (task 1). Produces: `is_expired(token: Token) -> bool`
Failing test: <the test, in full>
Implementation: <the code, in full>
Run: `pytest tests/test_auth.py -k expired` prints `1 passed`
```

Shape each failing test by what the tree already has. A task that fixes
behaviour an existing test covers adds a parametrized case with a readable id to
that test, not a new test function; a new function is for a new behaviour. Name
the shared helper or fixture the test uses, found by looking in the repo's test
support, rather than writing out a copy of one. The repo's own testing guidance
(its `CLAUDE.md`, `AGENTS.md` or CONTRIBUTING) says where they live.

Write for an engineer who is a strong developer and knows nothing about this
codebase. "Add appropriate error handling", "similar to task 3", "write tests
for the above" are plan failures — the reader may be reading your tasks out of
order and cannot resolve any of them.

If your instructions name an intent tree, the plan contains a task that applies
the spec's requirement changes to the tree and writes the tests they are pinned
to, each test seen to fail for the stated reason before the code makes it pass.

## Slicing a large plan

Slices don't replace sub-agents. Each slice becomes a child work item, with its own worktree
and branch, its own setup, its own implementer session and the nodes after it, and a join that
replays its commits. That overhead pays only when every slice is large enough to be a work item
of its own: several tasks, the size of a pull request someone would review by itself. Smaller
independent tasks stay unsliced: the implementer builds them in one session and can hand them to
sub-agents there, which share its worktree, need no setup or join, and run in parallel when
dispatched together. When in doubt, don't
slice.

When the plan is large, its parts don't depend on each other, and each part is that big, end it
with a `## Slices` section, one bullet per slice. Kraft builds the slices in parallel, each by
its own agent in its own worktree, then joins them:

```
## Slices

- **Slice 1: Parser.** Tasks 1-3.
- **Slice 2: CLI verb.** Tasks 4, 6.
```

Keys run 1, 2, 3... and every slice has a title. Tasks are the numbers of your `### Task N:`
headings; a range takes `-`. Slices must be **independent**: each starts from the same commit
and sees none of another's work, so a slice may not use a helper another slice creates. Put
shared groundwork (a helper two slices would both write, a schema both need) in tasks outside
every slice; Kraft builds those first and tests them before any slice starts. Never write that
one slice "comes after" another: that is refused. Use at most six slices, and at least two.
When the plan is small, its tasks build on each other, or a slice would hold one small task,
write no section at all.

## Check your own plan before you finish

Walk the spec section by section and point at the task that implements each
one. A section with no task is a gap: add the task. Then check that a name you
used in a late task is spelled the same way as where you defined it. Last,
check that every task has its `### Task N: title` heading and a failing test of
its own: Kraft does not count a task without the heading, and a task without a
test gives its reviewer nothing to run.
