---
name: status
description: "Reports where a Kraft Lite chain has got to (its node, running or blocked at a gate, attempts spent) and, on request, what a finished run took. Use when asked where a Kraft Lite chain is, whether it is stuck, or how long it ran; `kraft-lite:next` continues a chain and `kraft-lite:gate` answers a gate."
---

# Where the chain is

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.

List every chain in the directory first:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains

That gives each chain's id, title, status and current node. Report all of them. A
status that silently covers one of two chains is worse than no status, and `state`
alone cannot see the others.

Then, for each chain:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>

Report, in a sentence or two: the node, whether it is running or blocked at a
gate, attempts spent against the cap if there is one, and which backend holds the
state. `status: unstarted` means no chain exists here yet - say that, rather than
that nothing is running.

For a chain that has finished - or when the human asks what a run has cost so far:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" summary --chain-id <id>

That adds per-node times, how much of each was spent waiting on a human at a
gate, attempts spent, gates answered and rejection notes. The run's wall-clock
`duration_seconds` is null until the chain is done - report the nodes it has
walked so far and say the total lands at the end, rather than calling an
unfinished run instant. It has no token or cost figures: Lite runs inside your
session and cannot see them.

Read-only. Do not advance, close, or approve anything from here - that is what
`kraft-lite:next` and `kraft-lite:gate` are for.
