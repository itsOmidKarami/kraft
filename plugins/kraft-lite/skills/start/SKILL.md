---
name: start
description: "Creates a new Kraft Lite chain for the work and hands it to the first node. Use when the user asks to start, begin or kick off new work or a new chain under Kraft Lite; continuing an existing chain is `kraft-lite:next`."
---

# Starting a chain

If `$CLAUDE_PLUGIN_ROOT` is unset, `kl.py` is two directories above this file;
it needs `python3` 3.10 or newer, and without one, tell the user and stop.

Run `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains` first. `start` doesn't check for
an existing V1 chain and will create a second one beside it, so if one is not
`done`, ask the user whether they mean to continue it (`kraft-lite:next`). Skip
that question when `kraft-lite:gate` handed you this start for a chain revision:
the user already chose to start beside the old chain. Before asking, run
`python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>` on it: a
`Run: python3 kl.py migrate` refusal means migrate first (below), then ask with
the new id it prints.

If `.kraft-lite/registry.yaml` exists with no `.kraft-lite/bindings.yaml`, or a
verb refuses with `Run: python3 kl.py migrate`, this directory is from an older
Kraft Lite: follow `kraft-lite:init`'s last section, `A pre-V1 directory:
migrate`, first. No bindings file is needed otherwise: every task has a default.

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" start --title "<the work>" --chain <id or path> --spec <path> --plan <path>

Pass each flag the user's words call for, and only those, picked before you run
it: the chain freezes at `start`, so a run that dropped their chain, spec or plan
is wrong for good.

- `--chain <id or path>` runs a chain other than the default: an id from
  `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" chains --available`, or a chain file path
  (`.yaml`, or with a `/`). The chain is frozen against this run, so editing it
  later does not retarget it.
- `--spec <path>` and `--plan <path>` hand over documents the user already has:
  the chain skips the nodes that would write them and the gates that approve them.

If `start` refuses, it says why and wrote nothing. For a missing file or an
unknown chain id, ask the user for the right one. For a bindings refusal, tell
the user what it names: `bindings.yaml` is theirs to change. Never run it again
with a flag dropped to get past the refusal.

It prints, for example,
`{"chain_id": "<id>", "backend": "jsonl", "chain": "default", "source": "bundled", "unused_bindings": []}`:

- `backend`: `bd`, or `jsonl` for a file under `.kraft-lite/`.
- `source`: where the chain came from: `bundled`, `user`, `repo` (the repo's own
  `.kraft/`, as it stands in the working tree) or the file's path.
- `unused_bindings`: `tasks.<key>` entries in `bindings.yaml` that match no task
  of this chain, a typo or a binding for another chain. Name each to the user;
  don't edit `bindings.yaml` or start again, and go on.

Say all of it, so the user knows where their state lives, which chain is theirs and
what it runs. Carry that id: every later verb takes `--chain-id <id>`, and a
directory holding two unfinished chains refuses to guess between them.

Confirm the chain exists: `python3 "$CLAUDE_PLUGIN_ROOT/kl.py" state --chain-id <id>`
should report its first node `open`. If it reports `unstarted` or refuses the
id, the id or the directory is wrong; do not start a second chain to compensate.

Then invoke `kraft-lite:next`. Starting a chain and stopping before the first node
leaves the user with a state file and nothing running.
