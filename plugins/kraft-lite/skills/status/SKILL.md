---
name: status
description: Use to report where a Kraft Lite chain has got to - which node is live, what is blocking it, how many fix attempts are spent.
---

# Where the chain is

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>

To find out what else is here:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains

That lists every chain in the directory with its title, status and current node.
Report all of them. A status that silently covers one of two chains is worse than
no status, and `state` alone cannot see the others.

Report, in a sentence or two: the node, whether it is running or blocked at a
gate, attempts spent against the cap if there is one, and which backend holds the
state. `status: unstarted` means no chain exists here yet - say that, rather than
that nothing is running.

For a chain that has finished - or when the human asks what a run has cost so far:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" summary --chain-id <id>

That adds per-node times, how much of each was spent waiting on a human at a
gate, attempts spent, gates answered and rejection notes. The
run's wall-clock `duration_seconds` is null until the chain is done - report the
nodes it has walked so far and say the total lands at the end, rather than calling
an unfinished run instant. It has no token or cost figures: Lite runs inside your
session and cannot see them.

Read-only. Do not advance, close, or approve anything from here - that is what
`next` and `gate` are for.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
