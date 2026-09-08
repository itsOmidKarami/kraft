---
name: spec
description: Turn a work item's brief into a design a human can approve or reject in one read.
---

# Writing a spec, headless

You are writing the design for one work item, with no human in the loop. A
human will read what you produce and either approve it or reject it with a
note. Write for that reader.

## Before you write

Your task instruction is the work item's title followed by its description. The
description is the brief — treat it as the requirement, not as a hint. If it is
empty, the title is all you have, and a title is a label: lean harder on reading
the code before deciding what the change is.

Read the code the change touches. Trace the actual flow end to end — the
files, the callers, the tests that cover them. A design argued from a guess
about the codebase is worse than no design, because it reads as authoritative.

## Asking

You cannot ask a question and keep working. If something is genuinely
undecidable from the repo — a product choice, a tradeoff only the requester
can settle — stop with status `needs_context` and put **every** open question
in the one `question` field, numbered. Stopping once per question costs a full
relaunch each time.

Do not stop for anything you can decide yourself. Pick the option a competent
engineer on this codebase would pick, and say in the spec that you picked it
and why.

## What the spec contains

- **The problem**, in the terms of this repo: what is broken or missing today,
  with file and function names.
- **The approach**, and the two or three you rejected, one sentence each on
  why. A reviewer disagreeing with the choice needs to see the alternatives to
  say so.
- **Sections scaled to their complexity.** A component that is three lines gets
  a sentence. Do not pad.
- **What is explicitly out of scope.** Half of what a reviewer rejects is scope
  they did not expect.
- **How it will be verified**: the tests that must exist, named.

Leave no "TBD", no "to be determined later", no section that describes what a
decision will be about instead of making it. If you cannot resolve something,
that is a `needs_context` stop, not a placeholder.

## If you are revising

An existing document at your output path means a human read it and asked for
changes. Their note leads your task instruction. Address it directly: change
what they objected to, and leave what they did not object to alone. Do not
rewrite the whole thing to look new.
