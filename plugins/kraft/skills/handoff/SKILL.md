---
name: handoff
description: Use when work agreed in this session should be handed to Kraft instead of
  done here - after a spec and plan are settled, when the task is too big for this
  session, or when the current repo needs connecting to Kraft first.
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://github.com/itsOmidKarami/kraft#install rather than reporting a
connection error.

# Handing work to Kraft

Kraft runs semi-autonomous work items as chains, with human gates. Hand work off
rather than doing it inline when it is large enough to want that structure.

1. `ensure_repo()` - connects the current repo if Kraft has not seen it.
   Idempotent, so call it every time rather than checking first.
2. `create_work_item(title, description=...)` - files the work. The title is a
   label; the description is the brief, and it is what the spec node writes its
   design from. Put the intent in the description rather than packing it into
   the title.

**`create_work_item` does not start anything.** The item lands paused and a
person starts it from the board. When you report back, say the work is *filed*,
not that it is underway - telling someone their work is running when nothing is
running is the one failure this whole surface is built to avoid.

## When the spec and plan already exist

If documents were written in this session or already live in the repo, attach
them at intake instead of letting Kraft re-run those phases:

    create_work_item(title, description=..., attachments=[
        {"kind": "spec", "path": ".engineering/specs/x.md"},
        {"kind": "plan", "path": ".engineering/plans/x.md"},
    ])

Kind is `spec` or `plan`, at most one of each. A path is resolved against the
repo and against the working tree you are standing in, so a document written in
a worktree can be attached exactly as you wrote it — relative to that tree, or
absolute.

Attaching a spec or plan trims the node whose gate it satisfies, so the person
is not asked to re-approve what they just agreed with you, and the implementing
agent is told to follow the documents rather than guess.

Before attaching a plan, skim it for a full-test-suite step (e.g. "run the
full test suite" / "run all tests" as a task, not a task's own targeted test).
The chain's `verify` node already runs the suite after every task with its own
fix loop, so a plan step doing the same is redundant. If you see one, mention
to the user that it's not advised and offer to strip it before attaching.
