---
name: triage
description: "Diagnoses why a Kraft work item stopped, failed or is stuck needing a person, and routes it to a retry, a raised cap or the right sibling skill. Use when someone asks why an item stopped, capped out or asked a question, or how to get it going again; a pending gate is kraft:review or kraft:gates, a running or paused item kraft:steer."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# A stopped work item

Find out why it stopped before deciding anything. Retrying without the cause
reruns the same failure, and a fix loop that already spent its attempts will
spend them again.

## 1. Read the stop

```bash
kraft view show ID --json          # status, current node, pending gate
kraft view events ID --type work_item_needs_human   # the stop's `reason`
kraft view logs ID -n 60           # the tail of the session that stopped it
```

The stop event's `reason` is the cause. A `budget` tag on it means a spend cap
stopped it, and a reason naming a time cap means that cap ran out: neither is a
defect. A `capped` tag means a fix loop spent its attempts
(`{cycles, attempts}`). `needs_context:` means an agent asked a
question and nobody answered. A status that is only a pending gate is not a
stop: hand it to `kraft:review` to decide, or `kraft:gates` once decided.

## 2. Say what you found

One sentence naming the node, the cause, and which of these it is. An illustration,
not wording to copy: "`verification` stopped because its fix loop spent 3 of 3
attempts on the same failing test, so it is a failed task: retry with a steer."

| Cause | Offer |
|---|---|
| Task failed, a fix loop spent its attempts, or the work is heading the wrong way | `retry`, with a steer naming what to change |
| `stuck:` or `judge:` | `escalate_work_item(message=...)` (CLI `kraft item escalate ID --message`), or `retry` with a steer |
| An agent asked a question | `resume_work_item(steer=<the answer>)` (CLI `kraft item resume ID --steer`) |
| Dollar cap | a person raises it: `raise_budget` (CLI `kraft item raise-budget`) raises it and retries |
| Time cap | a person raises it: `set_work_item_policy(policy={"time_cap_minutes": N})` (CLI `kraft item set-policy ID --policy time_cap_minutes=N`), then `retry`. The call replaces the item's whole override, so send its existing `policy_override` fields too. A total time cap is `total_time_cap_minutes` |
| `rate_limit retries exhausted` | wait for the limit to reset, then `retry` |
| Config or install problem (missing skill, unresolved chain) | `kraft:check`, or `kraft:doctor` if the server itself is unwell |
| Anything else (a conflict, a closed merge request, a forge login) | the reason's entry at https://kraft-sdlc.dev/troubleshooting/why-did-my-item-stop |

## 3. Retry only when the person says to

A request to diagnose it or get it going isn't the go-ahead: say the retry and
its steer, then wait. `retry_work_item(steer="...")` (CLI `kraft item retry
[ID] --steer "..."`) reruns the node it stopped on. `path="node.step.task"`
reruns from a named point, and `restart=True` reruns the whole chain, so use
those only when the cause sits further back than the stopped node. Write the steer the way `kraft:steer` says:
it leads the retry's prompt and nothing else reaches the agent.

After a retry, check `kraft view show ID`. A stop with the same reason means the
steer did not reach the cause: report it rather than retrying again.

`skip_work_item` (CLI `kraft item skip`) advances past a node without running it.
That is the person's decision every time: offer it, do not take it.

Report what you found even when you do not retry.

## If you are a Kraft worker session

You cannot act on the item that is running you. Report the cause and let the
person decide.
