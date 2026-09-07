---
name: next
description: Use to run the next node of a Kraft Lite chain, and to resume one after a gate, a new session, or a cleared context - reads its whole state from disk, so it is always safe to call.
---

# Running the next node

This skill holds no state in the conversation. Everything comes from the chain
artifact and the state records, which is what makes it survive a `/clear`.

## 1. Read the state

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>

Every command in this skill takes `--chain-id`. Omit it only when you know this
directory holds one chain. If a verb exits saying there is more than one
unfinished chain, show the human the list it printed and ask which - do not pick.

You get `node`, `hooks`, `gate`, `attempt`, `cap`, `status` and `note`.

- `status: unstarted` - there is no chain here. Invoke the `start` skill, or say
  so if you were not asked to start one. Do not report this as finished.
- `status: done` - the chain is finished. Report the summary (below) and stop.
- `status: blocked` - a gate is waiting. Invoke the `gate` skill. Do not proceed.
- `note` non-empty - the node was rejected. That note leads this attempt.

## 2. Run the node's hooks, in order

For each hook in `hooks`, look it up in `.kraft-lite/registry.yaml` and dispatch:

- `kind: skill` - invoke that skill. If it is not installed, follow the entry's
  `prompt` instead and say you fell back.
- `kind: prompt` - follow the instruction inline.
- `kind: subprocess` - run the command, show its output.
- `kind: agent` or `kind: builtin` - these are Kraft's, not Lite's. Stop and tell
  the human which hook is bound to one; do not improvise a substitute.
- not in the registry at all - stop and say which hook has no entry. Do not
  improvise a handler for it. This chain froze at `start`, and its nodes
  cannot be reordered or removed. But `.kraft-lite/registry.yaml` is re-read on
  every dispatch, so rebinding the hook there is how a live chain is corrected.

The node records which hooks it has, not which have run, so resuming replays them
from the first. Keep hooks idempotent, and treat a hook that is not as a reason to
stop rather than to run it twice.

## 3. Handle the result

All hooks succeeded:

- `gate` is set - `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" gate --name <gate> --chain-id <id>`, then
  invoke the `gate` skill. Stop.
- `gate` is null - `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" close --chain-id <id>`, then run this
  skill again for the next node.

A hook failed and `cap` is set:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" attempt --chain-id <id>

If the output has `over_cap: true`, stop the chain and escalate: show the human
every attempt's output, not a summary of it. A cap that is hit is a failure they
need the traces for. Otherwise fix the cause and re-run this node's hooks only -
not the whole chain.

A hook failed and `cap` is null: stop and report. A node with no fix loop has no
retry budget to spend.

## 4. When the chain is done, report what the run cost

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" summary --chain-id <id>

Render it as a short table - one row per node with its time, how much of that time
was `blocked_seconds` waiting on a human, attempts against the cap where there is
one, its gate, and any rejection note - then a line of totals: nodes walked,
wall-clock duration, time spent waiting on a human, attempts spent, gates
answered, rejections.

`blocked_seconds` is normally part of the node's `seconds`, not extra to it. Say
which of the two the run actually went on - in a gated chain the waiting is usually
the larger, and reporting the total alone bills the agent for the human's hours.

After a rewind the two stop nesting: waiting is banked across every round the node
ever had, while `seconds` is re-timed from the redo, so `blocked_seconds` can
exceed it. Both numbers are true - report them as the node's whole history against
its last run, not as a split.

A node's `attempts` is every attempt it ever cost, so after a rewind it can exceed
the `cap`, which is the budget the node has now. That is the report working: the
retries a rewind cleared are the ones the run paid for.

Times are stamped a second apart at the coarsest, so a fast chain reporting about
a second a node is the clock's floor, not a measurement. A node redone after a
rejection is timed from the redo. Say so rather than presenting the number flat.

This covers the chain, not the conversation: it has no token count, cost, or turn
count, because Lite runs inside your session and never sees them. Do not estimate
them - if the human wants those, they come from the harness.

## `on.ci.poll` never blocks

Never sit in a synchronous loop waiting for checks. Do not idle - the human is
sitting here, and their attention is the resource this whole mode is spending.

Blocking and waiting are not the same thing. If your harness can wait in the
background and wake you when the wait finishes, use it: start the poll in the
background, say you have done so, and report once when the pipeline settles. That
costs the human nothing.

If your harness has no background primitive, run the check once, report, and tell
the human to invoke this skill again when it finishes. Making them re-invoke by
hand is the fallback, not the design.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
