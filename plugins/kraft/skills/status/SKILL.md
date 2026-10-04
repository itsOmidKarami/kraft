---
name: status
description: "Reports which phase a Kraft work item is in and what runs next, and watches it until it finishes. Use when someone asks where a work item has got to or wants a handed-off item watched; for why it stopped, use kraft:triage."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://itsomidkarami.github.io/kraft/get-started/install rather than reporting a
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

Two illustrations of what it prints (the node names are your chain's own). A gate's
node is the gate itself, so the second item is waiting on a person, not running:

```text
active: implementation → next verification
needs_human: plan_approval → next chain_revision · gate plan_approval
```

Drop the id to ask about the work item you are standing in: `kraft view show --json`
resolves it from the worktree. Without `jq`, the `get_work_item()` tool returns the
same fields.

## Watching it until it ends

Arm a monitor on:

```bash
kraft view events ID -f
```

Do not pass `--type`. `-f` ends itself on `work_item_completed` or
`work_item_abandoned`, so the monitor disarms on its own; a type filter would go
silent through exactly the escalation the person needs to hear about. A chain
emits tens of events over its life, not thousands. Add `--json` for NDJSON, one
object per line.

## What to report

Say the phase and the next node. If a gate is pending, say which one and that it
is waiting on a person - a work item sitting at a gate is not stuck, and calling
it stuck sends someone looking for a fault that is not there. If the status says
the item has stopped or needs a person, hand over to `kraft:triage` to find out why.
