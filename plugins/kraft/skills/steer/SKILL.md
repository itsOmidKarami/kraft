---
name: steer
description: "Use when a Kraft work item is heading the wrong way, or its spec or plan needs revising, and someone wants to redirect it."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://itsomidkarami.github.io/kraft/get-started/install rather than reporting a
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
original file changes nothing until it is re-attached. Check the plan for the
same wording, then confirm the stored copy with `kraft view docs ID`. Once the item has
started the call answers 409: its worktree already holds the documents,
committed on its branch, and steering is the only route.

## Started: stop, then restart with a steer

- Running and going wrong: `pause_work_item()`, then `resume_work_item(steer="...")`.
- Already stopped: `retry_work_item(steer="...")`, see `kraft:triage`.
- Several agent tasks paused, each needing different guidance: `resume` takes
  `steers={"node.step.task": "..."}` (CLI `--steer-task PATH=TEXT`, repeatable).

## Writing the steer

The steer leads the next attempt's prompt and is all that reaches the agent, so
it has to stand alone. Give one concrete instruction: what to change, in which
file or behaviour, and what to leave alone. "Do it better" gives the agent
nothing to act on; "the retry in `sync.py` swallows the timeout, let it
propagate and update the test" does. Say what was wrong about the last
attempt only when the agent would otherwise repeat it.

Ask the person before pausing: it discards the running attempt. Afterwards,
check `kraft view show ID` reads active again and `kraft view events ID` shows
the resume; a steer that never reached a new attempt did nothing.

## If you are a Kraft worker session

You cannot pause or resume the item that is running you. Report and let the
person decide.
