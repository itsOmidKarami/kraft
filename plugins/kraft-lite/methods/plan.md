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
Open each file you name before you name it: a line range or helper you did not
read is a guess the implementer will trust.

## If you are revising

The note on the existing document, a person's or an automated reviewer's,
leads your task instruction. Change what it objected to and leave the rest
alone, rather than rewriting the whole thing to look new.

## What the plan contains

A list of tasks, each under a `### Task N: title` heading numbered 1, 2, 3... in
order, with none skipped: Kraft finds task N as the Nth heading, to report
progress and to tell the implementer which task is which. A task is the smallest unit that carries its own test cycle
and is worth a fresh reviewer's gate. Fold setup, configuration and
documentation into the task whose deliverable needs them. Split only where a
reviewer could reject one task and approve its neighbour.

Each task names:

- the exact files it creates, modifies (with line numbers) and tests;
- what it consumes from earlier tasks and what later tasks consume from it —
  exact names, exact types;
- the failing test, written out in full;
- the implementation, written out in full;
- the command that runs the test, what it fails with before the
  implementation, and what it prints once it passes.

A shape to follow, with invented names:

```
### Task 2: Reject expired tokens at login
Files: modify `app/auth.py:40-62`; test `tests/test_auth.py`
Consumes: `Token.expires_at: datetime` (task 1). Produces: `is_expired(token: Token) -> bool`
Failing test: <the test, in full>
Implementation: <the code, in full>
Run: `pytest tests/test_auth.py -k expired` fails with `ImportError: cannot import name 'is_expired'`, then prints `1 passed`
```

Shape each failing test by what the tree already has. A task that fixes
behaviour an existing test covers adds a parametrized case with a readable id to
that test, not a new test function; a new function is for a new behaviour. Name
the shared helper or fixture the test uses, found by looking in the repo's test
support, rather than writing out a copy of one. The repo's own testing guidance
(its `CLAUDE.md`, `AGENTS.md` or CONTRIBUTING) says where they live. A task that
keeps behaviour as it is (a rename, a move) names the existing test that covers
it, passing before and after, and writes no new one.

Write for an engineer who is a strong developer and knows nothing about this
codebase. "Add appropriate error handling", "similar to task 3", "write tests
for the above", "... rest unchanged" and "TBD" are plan failures — the reader may be reading your tasks out of
order and cannot resolve any of them.

If your instructions name an intent tree, the plan contains a task that applies
the spec's requirement changes to the tree and writes the tests they are pinned
to, each test seen to fail for the stated reason before the code makes it pass.

## Slicing a large plan

Slices don't replace sub-agents. Each slice becomes a child work item, with its own worktree
and branch, its own setup, its own implementer session and the nodes after it, and a join that
replays its commits. Independent tasks that don't earn that overhead stay unsliced: the
implementer builds them in one session and can hand them to sub-agents there, which share its
worktree, need no setup or join, and run in parallel when dispatched together. Each task's
`Consumes` line is how the implementer tells which tasks are independent, so keep it exact.

End the plan with a `## Slices` section only when both of these hold; otherwise write no
section.

- The slices don't use each other's work.
- Every slice makes sense on its own: a coherent part with its own tests, which its own
  implement, verify and review loop can check. Think of it as a part you would file as its
  own work item if it didn't have to land with the others, held to a lower floor than one.
  Task and line counts are weak hints, not the test; a slice of one or two tasks almost
  never qualifies.

One bullet per slice:

```
## Slices

- **Slice 1: Parser.** Tasks 1-3.
- **Slice 2: CLI verb.** Tasks 4, 6-7.
```

Keys run 1, 2, 3... and every slice has a title. Tasks are the numbers of your `### Task N:`
headings; a range takes `-`. A slice bullet holds only the bold title and its task list, and
the section holds no other bullets: Kraft reads every number after the title as a task. Each slice starts from the same commit, so put shared groundwork
(a helper two slices would both write, a schema both need) in tasks outside every slice; Kraft
builds those first and tests them before any slice starts. Never write that one slice "comes
after" another: Kraft won't use such a section. Use at least two slices, and at most six by default.

## Check your own plan before you finish

Walk the spec section by section and point at the task that implements each
one. A section with no task is a gap: add the task. Then check that a name you
used in a late task is spelled the same way as where you defined it. Last,
check that every task has its `### Task N: title` heading and a test of its own
(a failing one, or the existing one a behaviour-keeping task names): Kraft does
not count a task without the heading, and a task without a test gives its
reviewer nothing to run. Check that every path, line range and helper you named
exists as written. If you wrote a `## Slices` section, check it against each
rule above, and that no task is in two slices: Kraft doesn't use a section that
breaks one, and the item then runs unsliced.
