---
name: gates
description: "Approves or rejects the gate a Kraft work item is waiting on (a spec, a plan, or the final review that merges the work), and files and submits review threads. Use when a person has made the call at a gate, said to request changes or ship it, or wants line comments sent; kraft:review helps decide first, kraft:steer pauses, resumes and redirects, and kraft:triage covers stopped items."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# Gates

A gate is the person's decision. Before `approve_gate`, `reject_gate`,
`skip_work_item` or `submit_review`, they have to have answered this gate in
this conversation: an instruction that names it counts, even a conditional one
(`kraft:review` says how to meet it). Your own recommendation, green CI or an
earlier general "go ahead" doesn't. Approving a gate because it seemed obvious
is how the gate stops meaning anything.

## Approve or reject

Only one gate pends at a time. Check that `get_work_item()`'s `pending_gate` is
the gate the person named before you call; if it isn't, tell them which one is
pending rather than deciding it.

- `approve_gate(gate=...)` - let the chain continue past that gate.
  A chain revision gate also needs `approve_gate(digest=...)`, with the
  `digest` that `get_gate_artifact()` returned alongside the revision the person
  read. If the revision changed since, the approval is refused: show it again.
  The final review (the gate that merges the work) needs
  `approve_gate(head=...)` too, with the `head` that `get_work_item()` returned
  as `review_head` when the person read the diff (`get_gate_artifact()` returns
  it as `head`). Skipping that gate takes `skip_work_item(head=...)`, and
  `submit_review` takes `head=` for an approve. If the branch moved since, the
  call is refused: show the change again.
- `reject_gate(note="...")` - send the chain back to the node that can address
  the note; `node=` overrides where it re-enters, at or before the gate's own. The
  note is required, because a rejection with no reason strands whoever picks the
  work up next.

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

## Starting a filed item

A filed work item lands paused. `resume_work_item()` starts it for the first
time, once the person asks. Pausing, resuming and steering a started item are
`kraft:steer`'s.

## Confirm it worked

After any of these, call `get_work_item()` and check the status and current
node moved as expected: an approved gate is no longer pending, a started item
is active. Report what you see, not what you
meant to cause.

## If you are a Kraft worker session

You cannot act on the work item that is running you - approve, reject, pause and
resume against your own item are all refused. Report what you found and let the
person decide.
