---
name: triage
description: "Use when a Kraft work item has stopped, failed or is stuck needing a person, and someone asks why or how to get it going again."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://github.com/itsOmidKarami/kraft#install rather than reporting a
connection error.

# A stopped work item

Find out why it stopped before deciding anything. Retrying without the cause
reruns the same failure, and a fix loop that already spent its attempts will
spend them again.

## 1. Read the stop

```bash
kraft view show ID --json          # status, current node, pending gate
kraft view events ID --type work_item_needs_human   # the stop's `reason`
kraft view logs ID -n 60           # the last session's tail
```

The stop event's `reason` is the cause. A `budget` or `capped` tag on it means a
spend or time cap, not a defect. `needs_context:` means an agent asked a
question and nobody answered. A status that is only a pending gate is not a
stop: hand it to `kraft:gates`.

## 2. Say what you found

One sentence naming the node, the cause, and which of these it is:

| Cause | Route |
|---|---|
| Task failed, or a fix loop spent its attempts | `retry`, with a steer naming what to change |
| An agent asked a question | `retry` with the answer as the steer |
| Budget, time cap, rate limit | a person raises the cap or waits; retrying only spends again |
| Config or install problem (missing skill, unresolved chain) | `kraft:check`, or `kraft:doctor` if the server itself is unwell |
| The work is heading the wrong way | `kraft:steer` |

## 3. Retry only when the person says to

`retry_work_item(steer="...")` (CLI `kraft item retry [ID] --steer "..."`) is the
only door back onto a stopped item; `resume` takes only a paused one. It reruns
the node it stopped on. `path="node.step.task"` reruns from a named point, and
`restart=True` reruns the whole chain, so use those only when the cause sits
further back than the stopped node. Write the steer as an instruction: it leads
the retry's prompt and nothing else reaches the agent.

`skip_work_item` advances past a node without running it. That is the person's
decision every time: offer it, do not take it.

Report what you found even when you do not retry.

## If you are a Kraft worker session

You cannot act on the item that is running you. Report the cause and let the
person decide.
