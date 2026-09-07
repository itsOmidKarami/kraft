---
name: start
description: Use when starting a new piece of work under Kraft Lite - materializes the chain's nodes as state records and runs the first node.
---

# Starting a chain

    python3 "$CLAUDE_PLUGIN_ROOT/kl.py" start --title "<the work>"

It prints the chain id and which backend holds the state (`bd`, or a JSONL file
under `.kraft-lite/`). Say which, so the human knows where their state lives.

If `.kraft-lite/registry.yaml` does not exist, run the `init` skill first. Do not
invent bindings.

Then invoke the `next` skill. Starting a chain and stopping before the first node
leaves the human with a state file and nothing running.

`$CLAUDE_PLUGIN_ROOT` is set when this loads as a plugin. If it is unset, `kl.py`
is two directories above this file - use that path instead of an empty one.
