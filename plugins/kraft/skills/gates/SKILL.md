---
name: gates
description: Use when a Kraft work item needs a human decision or has gone wrong -
  approving or rejecting the gate it is waiting on, or pausing and resuming work that
  is heading in the wrong direction.
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://itsomidkarami.github.io/kraft/get-started/install rather than reporting a
connection error.

# Gates and steering

## Gates

- `approve_gate()` - let the chain continue past the gate it is waiting on.
  A chain revision gate also needs `approve_gate(digest=...)`, with the
  `digest` that `get_gate_artifact()` returned alongside the revision the person
  read. If the revision changed since, the approval is refused: show it again.
- `reject_gate(note="...")` - send it back to be re-planned. The note is
  required, because a rejection with no reason strands whoever picks the work up
  next.

**Ask the person before calling either.** A gate exists precisely because this is
a decision a human makes. Read them the diff or the plan, get an answer, then act
on it. Approving a gate because it seemed obvious is how the gate stops meaning
anything.

## Review threads

Feedback about specific lines belongs in review threads, not in one long reject
note. Threads work at any point in an item's life, gate or no gate:

- `add_review_comment(body=..., file_path=..., start_line=..., label="must_fix")`
  files a draft thread; `thread_id=...` makes it a reply instead. `label` is
  `must_fix`, `question` or `nit`; `suggestion` proposes replacement text for
  the thread's lines. Nothing is sent until the review is submitted.
- `submit_review(outcome=...)` sends every draft on the item:
  - `comment` queues them for the next agent that runs, without stopping
    anything. At a pending gate it also launches an agent to answer them.
  - `request_changes` redoes work: at a gate it rejects the gate with the
    threads as the note. With no gate it picks the node the threads are about
    and re-runs it now if that node is the one running or the item is stopped;
    an earlier node waits until the running one finishes, and a paused item
    until it is resumed. The response's `target` and `action` say which.
  - `approve` needs a pending gate, and is refused while any `must_fix` thread
    is unresolved.
- `list_threads()` shows what is open and what the agents replied;
  `resolve_thread()` and `reopen_thread()` settle a thread once a reply holds up
  or does not.

`submit_review` is a gate-level decision like `approve_gate`: ask first.

## Steering

There is no channel into a running agent, so redirecting work means stopping it
and starting it again with new context:

- `pause_work_item()` - stop the current attempt.
- `resume_work_item(steer="...")` - start again, with the steer leading the next
  attempt's prompt.

`resume_work_item()` is also how a freshly filed work item is started for the
first time.

## Confirm it worked

After any of these, call `get_work_item()` and check the status and current
node moved as expected: an approved gate is no longer pending, a paused item
reads paused, a resumed one is active again. Report what you see, not what you
meant to cause.

## If you are a Kraft worker session

You cannot act on the work item that is running you - approve, reject, pause and
resume against your own item are all refused. Report what you found and let the
person decide.
