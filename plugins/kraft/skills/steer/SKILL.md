---
name: steer
description: "Redirects a Kraft work item by revising its spec or plan before it starts, or by pausing it and resuming it with a steer once it has. Use when a running or paused work item is off track, needs pausing or resuming, or its spec or plan needs revising; a stopped item is kraft:triage, a gate waiting on a person kraft:gates."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# Redirecting a work item

There is no channel into a running agent. Redirecting means changing what the
next attempt starts from, and which way depends on whether the item has
started.

## Not started yet: revise the documents

```bash
kraft item set-attachments [ID] --spec PATH     # --plan likewise
kraft item set-attachments [ID] --drop spec     # removes it, puts its gate back
```

Filing snapshots the spec and plan into Kraft's storage, so editing the
original file changes nothing until it is re-attached. If the spec changed,
check whether the plan repeats the old wording, then confirm the stored copy
with `kraft view docs ID --attachment spec` (or `plan`; `get_attachment` over
MCP). Once the item has started the call answers 409: its worktree already
holds the documents, committed on its branch, and steering is the only route.

## Started: stop, then restart with a steer

Ask the person before pausing, unless they said pause or stop: the turn in
flight stops, and on resume the agent picks its session back up if it can, else
restarts the task.

- Running and going wrong: `pause_work_item()`, then `resume_work_item(steer="...")`.
  A pause that stopped no agent task (a test run, a CI wait) takes no steer:
  `steerable` in `kraft view show ID --json` reads false and a steer is refused.
  Resume without one and tell the person the steer waits for the next agent
  task.
- Waiting at a gate: it can't be paused. Redirect it by rejecting the gate with
  a note (`kraft:gates`).
- Already stopped: that's `kraft:triage`, which retries with a steer written as
  below.
- Several agent tasks paused, each needing different guidance: `resume` takes
  `steers={"node.step.task": "..."}` (CLI `--steer-task PATH=TEXT`, repeatable).

## Writing the steer

The steer leads the next attempt's prompt and is all that reaches the agent, so
it has to stand alone. Give one concrete instruction: what to change, in which
file or behaviour, and what to leave alone. "Do it better" gives the agent
nothing to act on; "the retry in `sync.py` swallows the timeout, let it
propagate and update the test" does. Say what was wrong about the last
attempt only when the agent would otherwise repeat it.

After resuming or retrying, check that `kraft view show ID` reads active again
and `kraft view events ID` shows the resume; a steer that never reached a new
attempt did nothing.

## If you are a Kraft worker session

You cannot pause or resume the item that is running you. Report and let the
person decide.
