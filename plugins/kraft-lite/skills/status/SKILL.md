---
name: status
description: "Reports where a Kraft Lite chain has got to (its node and kind, the steps it runs or the gate it waits at, attempts spent, and a sliced run's slices and held questions) and, on request, what a finished run took. Use when asked where a Kraft Lite chain or its slices are, whether it is stuck, or how long it ran, and when `kraft-lite:run-slices` reports; `kraft-lite:next` continues a chain and `kraft-lite:gate` answers a gate."
---

# Where the chain is

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
Every command here runs on `python3` 3.10 or newer; if it is missing or older, tell the
human and stop, because Lite has no other runner.

List every chain in the directory first:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains

That gives each chain's id, title, status and current node. Report all of them. A
status that silently covers one of two chains is worse than no status, and `state`
alone cannot see the others.

Then, for each chain:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>

Report, in a sentence or two: the node and its `kind`, whether it is running or
blocked, and which backend holds the state. For an exec node, its `steps` in
order with what each task runs (its skill, prompt or command), and attempts spent
against `cap` if there is one. For a gate node, `gate.message`, the file at
`gate.artifact_path`, and that a rejection goes back to `gate.reject_to`.
`status: unstarted` means no chain exists here yet: say that, not that nothing runs.

An illustration, not a template: "`Add export` is at `plan_approval`, a gate,
blocked: review and approve the plan, `docs/plan.md`; a rejection goes back to
`plan`. State in `jsonl`. It needs your answer before anything runs."

If `state` shows `sliced` not null, also run these. `--mark` is refused together
with `--held`, so they are separate commands:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" slices --chain-id <id>
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" decisions --held --chain-id <id>
    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" decisions --unreported --mark --chain-id <id>

Report held questions first, each with why it is held and how to answer it: they
wait on the user, and stay at the top until answered. A slice's `after` slices not
yet merged are what it waits on; one in `ready` is ready to start, and one in
`early` may start now from the branch of the one slice it comes after. Then
what starts or merges next, the decisions since the last report, and what the
user needs to do.
A held question is also in the `--unreported` list: report it once, under held.
A held push is answered in that slice's own session, where the user types
`You may push to <branch>.`; any other held question, to the controller. The
shape, in this order:

    Held for you (first, every time until answered):
    - <slice>: <question> - <why held>. To answer: <exact words, and where>
    Slices:
    - <key> <title>: waiting on <keys> | ready to start | can start early from <key> | running (<session>) | in review | ready to merge | merged
    - rebase pending: <key> onto <its cut_from> (each key in `base_moved`)
    Next: <what starts or merges next>
    Decided since the last report:
    - <slice>: <question> -> <answer> (<why>)
    Needs from you: <each action, with the session and the words>

For a chain that has finished - or when the human asks what a run has cost so far:

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" summary --chain-id <id>

That adds per-node times, the waiting on a human at gates, attempts, gates
answered and rejection notes. `duration_seconds` is null until the chain is done:
report the nodes walked so far and say the total lands at the end. It has no
token or cost figures: Lite runs inside your session and cannot see them.

Advance, close, approve, answer or start nothing from here, not even a slice in
`ready` or `early`: that is what `kraft-lite:next`, `kraft-lite:gate` and
`kraft-lite:run-slices` are for. When `kraft-lite:run-slices` invoked you,
return to it after the report.
