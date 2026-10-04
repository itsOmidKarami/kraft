---
name: plan
description: "Writes the implementation plan for a work item from its human-approved spec, as small independently reviewable tasks. Kraft runs it as the library's plan_author task, after the spec gate and before implementation. Use when a chain needs that plan written or revised."
---

# Writing a plan, headless

The spec for this work item is already written and already approved by a human:
read `.engineering/specs/` for the document whose name matches this work item
before you do anything else. If it is not there, the chain skipped the spec node
because a human attached one at intake — look for the attached document instead.
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

## Check your own plan before you finish

Walk the spec section by section and point at the task that implements each
one. A section with no task is a gap: add the task. Then check that a name you
used in a late task is spelled the same way as where you defined it. Last,
check that every task has its `### Task N: title` heading and a failing test of
its own: Kraft does not count a task without the heading, and a task without a
test gives its reviewer nothing to run.
