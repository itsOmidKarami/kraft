---
name: spec
description: "Writes the design spec for one work item from its brief, headless, for a human to approve or reject. Kraft runs it as the library's spec_author task, before the plan is written; use when a work item's brief needs a design before planning."
---

# Writing a spec, headless

You are writing the design for one work item, with no human in the loop. A
human will read what you produce and either approve it or reject it with a
note. Write for that reader.

## Before you write

Your instructions carry the work item's title, then its description. The
description is the brief — treat it as the requirement, not as a hint. If it is
empty, the title is all you have, and a title is a label: lean harder on reading
the code before deciding what the change is.

Read the code the change touches. Trace the actual flow end to end — the
files, the callers, the tests that cover them. A design argued from a guess
about the codebase is worse than no design, because it reads as authoritative.

## Asking

Stop with `needs_context` only for what the repo cannot settle: when the
options differ in what the requester gets (behaviour, scope, cost) and neither
the code nor the brief ranks them. How it is built is yours to decide.

Do not stop for anything you can decide yourself. Pick the option a competent
engineer on this codebase would pick, and say in the spec that you picked it
and why.

## What the spec contains

- **The problem**, in the terms of this repo: what is broken or missing today,
  with file and function names.
- **The approach**, and the real alternatives you rejected, one sentence each
  on why; say so if there was only one way. A reviewer disagreeing with the choice needs to see the alternatives to
  say so.
- **Sections scaled to their complexity.** A component that is three lines gets
  a sentence. Do not pad.
- **What is explicitly out of scope.** Half of what a reviewer rejects is scope
  they did not expect.
- **How it will be verified**: the tests that must exist, named.

The spec holds the decisions the plan builds on: behaviour, interfaces, data
shapes, files. Task order and code are the plan's.

If your instructions name an intent tree, the spec also states the requirement
changes this work makes to it: each requirement it adds, changes or removes, one
behaviour per requirement, in the tree's format and no broader than a test will
enforce. Where no capability file covers the behaviour yet, propose a new one.

Leave no "TBD", no "to be determined later", no section that describes what a
decision will be about instead of making it. If you cannot resolve something,
that is a `needs_context` stop, not a placeholder.

Before you finish, check that every file and function you cite exists as
written (grep for it), that each behaviour in the approach has a named test, and
that no placeholder is left.

## If you are revising

The note on the existing document, a person's or an automated reviewer's,
leads your task instruction. Change what it objected to and leave the rest
alone, rather than rewriting the whole thing to look new; if the change alters
the approach, bring the sections that depended on it back in line.
