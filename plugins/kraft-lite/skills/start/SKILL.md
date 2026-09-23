---
name: start
description: "Use when the human asks to begin new work under Kraft Lite."
---

# Starting a chain

If `.kraft-lite/registry.yaml` does not exist, invoke `kraft-lite:init` first. Do
not invent bindings, and do not start without it: `start` only warns when the
registry is missing. If `start` exits with "hooks with no registry binding",
rerun `kraft-lite:init` or edit the registry.

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" start --title "<the work>"

To run a chain other than the packaged default, add `--chain <path-to-chain.json>`.
The template is frozen against this run, so editing that file later does not
retarget a chain already in flight.

It prints the chain id and which backend holds the state (`bd`, or a JSONL file
under `.kraft-lite/`). Say both, so the human knows where their state lives and
which chain is theirs.

Carry that id. Every later verb in this chain takes `--chain-id <id>`, and a
directory holding two unfinished chains refuses to guess between them.

Then invoke `kraft-lite:next`. Starting a chain and stopping before the first node
leaves the human with a state file and nothing running.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
