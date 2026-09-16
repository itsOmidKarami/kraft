---
name: gates
description: Use when a Kraft work item needs a human decision or has gone wrong -
  approving or rejecting the gate it is waiting on, or pausing and resuming work that
  is heading in the wrong direction.
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://github.com/itsOmidKarami/kraft#install rather than reporting a
connection error.

# Gates and steering

## Gates

- `approve_gate()` - let the chain continue past the gate it is waiting on.
- `reject_gate(note="...")` - send it back to be re-planned. The note is
  required, because a rejection with no reason strands whoever picks the work up
  next.

**Ask the person before calling either.** A gate exists precisely because this is
a decision a human makes. Read them the diff or the plan, get an answer, then act
on it. Approving a gate because it seemed obvious is how the gate stops meaning
anything.

## Steering

There is no channel into a running agent, so redirecting work means stopping it
and starting it again with new context:

- `pause_work_item()` - stop the current attempt.
- `resume_work_item(steer="...")` - start again, with the steer leading the next
  attempt's prompt.

`resume_work_item()` is also how a freshly filed work item is started for the
first time.

## If you are a Kraft worker session

You cannot act on the work item that is running you - approve, reject, pause and
resume against your own item are all refused. Report what you found and let the
person decide.
