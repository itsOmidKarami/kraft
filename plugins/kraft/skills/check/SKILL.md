---
name: check
description: "Use when a repo's Kraft config may have drifted from what this Kraft version ships, or after a Kraft upgrade."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://itsomidkarami.github.io/kraft/get-started/install rather than reporting a
connection error.

# Checking a repo's Kraft config

Run `kraft admin templates lint` and `kraft admin doctor`. Between them they
carry the drift this skill exists to narrate:

- **lint** - every chain in `~/.kraft/templates/chains/` that does not resolve
  against `library.yaml`, each with its reason: a reference to a component that
  isn't declared, a component extending one of another kind, a policy a chain
  widens past `policy.yaml`. It exits 1 on any error. A chain that does not
  resolve cannot be filed, so this comes first.
- `chain_templates` (doctor) - a node a shipped chain has that the live copy in
  `chains/` doesn't. It compares authored node **ids only** - a live node that
  kept its id but lost a task, or whose `extends` now points somewhere else, is
  invisible to it. A clean row is not proof the chain matches; `kraft admin
  templates show ID --resolved` is what the chain actually runs.
- `agent: <profile>` (doctor) - a harness profile some chain selects whose
  executable is not on PATH, or which `harnesses.yaml` does not declare.
- `capabilities` (doctor) - capabilities this Kraft has that the operator's
  seeded config predates. `templates/` is seeded once and never overwritten, so
  an install keeps its original library and chains forever. Read the row's
  `-> ` lines out as the edit each one needs; they are adoption instructions,
  not drift to be "fixed". Never offer to copy the shipped defaults over: a
  live library carries per-task `model`/`effort` choices the shipped defaults
  do not, and overwriting destroys them.
- `setup <repo>` (doctor) - a connected repo with no `setup_command` in
  `repos.yaml`. There is no default, so this repo's next work item stops when
  its worktree is built. The row carries a suggestion probed from the repo's
  own markers; check it against what the repo actually needs before writing it
  in, the same way step 1 of `kraft:onboard` checks a probed `test_command`.

Translate each row's compact detail (`c1, c2 missing` style) into one line per
chain or node, grouped under its own heading, so a human reads "these three
things are stale" rather than a string they have to parse themselves.

## What doctor cannot check

For every agent task in the live library and chains
(`~/.kraft/templates/library.yaml` and `chains/*.yaml`) whose `skill: X` names
a skill where `X` contains a `:` - a reference into this agent's own plugin
system, not a method Kraft ships - check `X` against the skills currently
available to you (the list your own session already has). The Kraft server
cannot answer this, by design; you can, because you are the agent that would
have to load it. `kraft admin templates show ID --resolved` prints
each task with the `skill:` it inherited, so read that rather than the raw
files.

List any `X` not in your available-skills list under its own "not installed
for this agent" heading. This is a per-agent fact, not a per-repo one - the
same library can be fully served on one machine and missing skills on another.

## If asked to fix something

Report-only until the human says to act. Then, for each item they pick:

1. State the exact edit before making it - which line in
   `~/.kraft/templates/library.yaml` or the live chain file under `chains/`,
   or which of `set_chain_template` / `set_node_overrides` /
   `set_agent_overrides` for a per-item override instead of the shared file.
2. Wait for confirmation.
3. Apply it, then re-run `kraft admin templates lint` and `kraft admin doctor`
   and confirm the row that named the problem now reads clean. Report the
   failure plainly if it doesn't - don't declare it fixed on the strength of
   having made the edit.

A missing plugin skill has no edit to propose here - it is installed or it
isn't. Say so, and name what needs installing, rather than inventing a
workaround.
