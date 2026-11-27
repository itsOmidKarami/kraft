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
kraft view show ID --json          # stop.kind, stop_reason, suggested_action, steerable, pending_gate
kraft view events ID --type work_item_needs_human   # the figures: `capped`, `budget`, `facts`
kraft view logs ID -n 60           # the tail of the session that stopped it; look for a login error
```

`stop_reason`'s first words pick the row below; `stop.kind` says what sort of
stop it is. `suggested_action` is Kraft's own proposal. A `budget` stop means a
spend cap stopped it, and a `cap` stop whose reason names a time cap means that
cap ran out: neither is a defect. A `cap` stop can also be a fix loop that spent
its attempts (`capped`: `{cycles, attempts}`). `needs_context:` means an agent
asked a question and nobody answered. Not every status is a stop. A pending gate
(`pending_gate` set) is not: hand it to `kraft:review` to decide, or
`kraft:gates` once decided. `waiting`, `queued`, `blocked`, `rate_limited` and
`paused` aren't stops either, and a sliced parent reads `waiting` with no stop
of its own: each of its slices (`kraft view show` lists them) stops for its own
reason.

## 2. Say what you found

One sentence naming the node, the cause, and which of these it is. An illustration,
not wording to copy: "`verification` stopped because its fix loop spent 3 of 3
attempts on the same failing test, so it is a failed task: retry with a steer."

| Cause | Offer |
|---|---|
| Task failed, a fix loop spent its attempts, or the work is heading the wrong way | `retry`, with a steer naming what to change. If the log shows an agent-CLI login error, the person signs in first, then `retry` with no steer. If `steerable` reads false, `retry` plain: a steer is refused (409) |
| `stuck:` or `judge:` | `escalate_work_item(message=...)`, or `retry` with a steer |
| An agent asked a question | put `needs_context_question` (in `kraft view show ID --json`) to the person; the answer is theirs: `resume_work_item(steer=<their answer>)` |
| Dollar cap | a person raises it: `raise_budget` raises the item's own cap or its policy's item-wide `budget_usd`, and retries. A 409 names the fix for any other cap |
| Time cap | a person raises it: `set_work_item_policy(policy={"time_cap_minutes": N})`, then `retry`. A total time cap is `total_time_cap_minutes` |
| `rate_limit retries exhausted` | wait for the limit to reset, then `retry` |
| Config or install problem (missing skill, unresolved chain) | `kraft:check`, or `kraft:doctor` if the server itself is unwell |
| Anything else (a conflict, a closed merge request, a forge login) | match the first words of the reason to the first column at https://kraft-sdlc.dev/troubleshooting#find-your-reason |

The time-cap call replaces the item's whole override, so send its existing
`policy_override` fields too. Every row is an offer: nothing in it runs until
the person agrees, and it runs through these MCP tools, not the `kraft item`
verbs from a shell, which Kraft records as the person's own call.

## 3. Retry only when the person says to

A request to diagnose it or get it going isn't the go-ahead: say the retry and
its steer, then wait. A retry resets the node's attempt counters and spends
again. `retry_work_item(steer="...")` reruns where it stopped (after a time or
budget cap, the step). `path="node.step.task"` reruns from a named point, and
`restart=True` reruns the whole chain, so use those only when the cause sits
further back than the stopped node. Write the steer the way `kraft:steer` says:
it leads the retry's prompt and nothing else reaches the agent.

After a retry the item reads `active` or `queued` first; `kraft:status` watches
it to the next stop. A stop with the same reason means the steer didn't reach the
cause: report it rather than retrying again.

`skip_work_item` advances past a node without running it, which drops a check
the chain exists to run. That is the person's decision every time: offer it,
don't take it.

Report what you found even when you don't retry.

## If you are a Kraft worker session

You can't act on the item that is running you. Report the cause and let the
person decide.
