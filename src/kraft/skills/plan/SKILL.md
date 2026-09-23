---
name: plan
description: "Use when a work item's spec is approved and the chain needs its implementation plan."
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

A list of tasks. A task is the smallest unit that carries its own test cycle
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
used in a late task is spelled the same way as where you defined it.
