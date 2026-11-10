---
name: status
description: "Reports which phase a Kraft work item is in and what runs next, and watches it until it finishes. Use when someone asks where a work item has got to, whether it has finished, or wants to be told when it does; the whole board is kraft:board, why it stopped kraft:triage."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# Where a Kraft work item has got to

## The phase line

```bash
kraft view show ID --json | jq -r '
  "\(.status): \(.current_node_id // "not started") → next \(.next_node_id // "done")" +
  (if .pending_gate then " · gate \(.pending_gate)" else "" end)
'
```

One line: the status, the node running now, the node coming next, and the gate
it is waiting on if there is one. `next_node_id` is null on the last node of the
chain, which renders as `done`; on an item nobody has started yet it is node
zero, because that is what starting it will run.

Three illustrations of what it prints (the node names are your chain's own). A
gate's node is the gate itself, so the second item is waiting on a person, not
running; the third was filed and nothing runs until a person starts it:

```text
active: implementation → next verification
needs_human: plan_approval → next chain_revision · gate plan_approval
paused: not started → next spec
```

Drop the id to ask about the work item you are standing in: `kraft view show --json`
resolves it from the worktree. Without `jq`, the `get_work_item()` tool returns the
same fields.

## Watching it until it ends

Run it as a background command and read its lines as they arrive (in Claude
Code, the Monitor tool); with no way to do that, re-read the phase line instead
of blocking on it:

```bash
kraft view events ID -f
```

Do not pass `--type`. `-f` ends itself on `work_item_completed` or
`work_item_abandoned`, so the monitor disarms on its own; a type filter would go
silent through exactly the escalation the person needs to hear about. A chain
emits tens of events over its life, not thousands. Add `--json` for NDJSON, one
object per line. It prints the history first, so trust the phase line for where
it is now. When a line arrives, tell the person about a gate, a stop or the end;
say nothing for a node starting or finishing.

## What to report

Say the phase and the next node, then by status:

- A gate pending: say which one and that it is waiting on a person. It isn't
  stuck, and calling it stuck sends someone looking for a fault that isn't
  there. `kraft:review` helps them decide, `kraft:gates` records it.
- `needs_human` with no gate: it has stopped; `kraft:triage` finds out why.
- `paused`, `queued` or `blocked`: not running, and not a fault; say which
  (`dependencies` names what a blocked item waits on; dropping one with `kraft
  item unblock` is the person's call). `kraft:gates` starts a filed item,
  `kraft:steer` resumes a paused one.
- `waiting` or `rate_limited`: Kraft is holding it, and ends the wait itself.
- `completed` or `abandoned`: say so.
