---
name: status
description: "Use when someone asks where a Kraft work item has got to, or wants a handed-off item watched until it finishes."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://github.com/itsOmidKarami/kraft#install rather than reporting a
connection error.

# Where a Kraft work item has got to

## The phase line

```bash
kraft view show ID --json | jq -r '
  "\(.status): \(.current_node_id) → next \(.next_node_id // "done")" +
  (if .pending_gate then " · gate \(.pending_gate)" else "" end)
'
```

One line: the status, the node running now, the node coming next, and the gate
it is waiting on if there is one. `next_node_id` is null on the last node of the
chain, which renders as `done`; on an item nobody has started yet it is node
zero, because that is what starting it will run.

Drop the id to ask about the work item you are standing in: `kraft view show --json`
resolves it from the worktree.

## Watching it until it ends

Arm a monitor on:

```bash
kraft view events ID -f
```

Do not pass `--type`. `-f` ends itself on `work_item_completed` or
`work_item_abandoned`, so the monitor disarms on its own; a type filter would go
silent through exactly the escalation the person needs to hear about. A chain
emits tens of events over its life, not thousands.

`--json` makes that stream NDJSON, one object per line.

## What to report

Say the phase and the next node. If a gate is pending, say which one and that it
is waiting on a person - a work item sitting at a gate is not stuck, and calling
it stuck sends someone looking for a fault that is not there.
