---
name: start
description: "Creates a new Kraft Lite chain for the work, on the default chain or one the human names, and hands it to the first node. Use when the human asks to begin new work under Kraft Lite; continuing an existing chain is `kraft-lite:next`."
---

# Starting a chain

Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains` first. `start` never checks for
an existing chain and will create a second one beside it, so if one is not
`done`, ask the human whether they mean to continue it (`kraft-lite:next`).

If `.kraft-lite/registry.yaml` exists with no `.kraft-lite/bindings.yaml`, or a
verb refuses with `Run: python3 kl.py migrate`, this directory is from an older
Kraft Lite: run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" migrate` first. Tell the
human it wrote `.kraft-lite/bindings.yaml`, to be committed, and each run it
moved to a new id. No bindings file is needed otherwise: every task has a default.

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" start --title "<the work>"

- `--chain <id or path>` runs a chain other than the default: an id from
  `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains --available`, or a chain file. The
  chain is frozen against this run, so editing it later does not retarget it.
- `--spec <path>` and `--plan <path>` hand over documents the human already has:
  the chain skips the nodes that would write them and the gates that approve them.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
Every command here runs on `python3` 3.10 or newer; if it is missing or older, tell the
human and stop, because Lite has no other runner.

It prints the chain id and which backend holds the state, for example
`{"chain_id": "<id>", "backend": "jsonl"}`: `bd`, or `jsonl` for a file under
`.kraft-lite/`. Say both, so the human knows where their state lives and which
chain is theirs. Carry that id: every later verb takes `--chain-id <id>`, and a
directory holding two unfinished chains refuses to guess between them.

Confirm the chain exists: `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>`
should report its first node `open`. If it reports `unstarted` or refuses the
id, the id or the directory is wrong; do not start a second chain to compensate.

Then invoke `kraft-lite:next`. Starting a chain and stopping before the first node
leaves the human with a state file and nothing running.
