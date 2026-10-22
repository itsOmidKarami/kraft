---
name: start
description: "Creates a new Kraft Lite chain for the work and hands it to the first node. Use when the user asks to begin new work under Kraft Lite; continuing an existing chain is `kraft-lite:next`."
---

# Starting a chain

If `$CLAUDE_PLUGIN_ROOT` is unset, `kl.py` is two directories above this file;
it needs `python3` 3.10 or newer, and without one, tell the user and stop.

Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains` first. `start` never checks for
an existing chain and will create a second one beside it, so if one is not
`done`, ask the user whether they mean to continue it (`kraft-lite:next`).

If `.kraft-lite/registry.yaml` exists with no `.kraft-lite/bindings.yaml`, or a
verb refuses with `Run: python3 kl.py migrate`, this directory is from an older
Kraft Lite: follow `kraft-lite:init`'s last section, `A pre-V1 directory:
migrate`, first. No bindings file is needed otherwise: every task has a default.

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" start --title "<the work>"

- `--chain <id or path>` runs a chain other than the default: an id from
  `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains --available`, or a chain file path
  (`.yaml`, or with a `/`). The chain is frozen against this run, so editing it
  later does not retarget it.
- `--spec <path>` and `--plan <path>` hand over documents the user already has:
  the chain skips the nodes that would write them and the gates that approve them.

It prints the chain id, which backend holds the state, and the chain it froze
with where that came from, for example
`{"chain_id": "<id>", "backend": "jsonl", "chain": "default", "source": "bundled", "unused_bindings": []}`:
`bd`, or `jsonl` for a file under `.kraft-lite/`; `bundled`, `user`, `repo` (the
repo's own `.kraft/`, as it stands in the working tree) or the file's path. Say
all of it, so the user knows where their state lives, which chain is theirs and
what it runs, and any `unused_bindings`: `bindings.yaml` keys no task of this
chain reads, often a typo. Carry that id: every later verb takes `--chain-id <id>`, and a
directory holding two unfinished chains refuses to guess between them.

Confirm the chain exists: `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>`
should report its first node `open`. If it reports `unstarted` or refuses the
id, the id or the directory is wrong; do not start a second chain to compensate.

Then invoke `kraft-lite:next`. Starting a chain and stopping before the first node
leaves the user with a state file and nothing running.
