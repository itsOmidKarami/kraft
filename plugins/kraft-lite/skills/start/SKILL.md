---
name: start
description: "Creates a new Kraft Lite chain for the work and hands it to the first node. Use when the human asks to begin new work under Kraft Lite; continuing an existing chain is `kraft-lite:next`."
---

# Starting a chain

If `.kraft-lite/registry.yaml` does not exist, invoke `kraft-lite:init` first. Do
not invent bindings, and do not start without it: `start` only warns when the
registry is missing. If `start` exits with "hooks with no registry binding",
rerun `kraft-lite:init` or edit the registry.

Before starting, run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains`. `start` never checks
for an existing chain and will create a second one beside it, so if one is not
`done`, ask the human whether they mean to continue it (`kraft-lite:next`).

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" start --title "<the work>"

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
Every command here runs on `python3` 3.10 or newer; if it is missing or older, tell the
human and stop, because Lite has no other runner.

To run a chain other than the packaged default, add `--chain <path-to-chain.json>`.
The template is frozen against this run, so editing that file later does not
retarget a chain already in flight.

It prints the chain id and which backend holds the state, for example
`{"chain_id": "<id>", "backend": "jsonl"}`. The backend is `bd`, or `jsonl` for a
file under `.kraft-lite/`. Say both, so the human knows where their state lives
and which chain is theirs.

Carry that id. Every later verb in this chain takes `--chain-id <id>`, and a
directory holding two unfinished chains refuses to guess between them.

Confirm the chain exists: `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>`
should report the first node (`spec`, in the packaged chain) as `open`, not
`unstarted`. If it reports `unstarted` or refuses the id, the id or the directory
is wrong; do not start a second chain to compensate.

Then invoke `kraft-lite:next`. Starting a chain and stopping before the first node
leaves the human with a state file and nothing running.
