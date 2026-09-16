---
name: check
description: Use to see whether a repo's Kraft config has drifted from what this Kraft version ships - a hook stuck on its placeholder, a chain template missing a node, or a hook naming a plugin skill that isn't installed. Report-only unless the human asks for a fix.
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://github.com/itsOmidKarami/kraft#install rather than reporting a
connection error.

# Checking a repo's Kraft config

Run `kraft admin doctor`. Two of its rows carry the drift this skill exists to
narrate:

- `hooks` - a hook this version ships a real binding for, still on
  `builtin:noop`, or missing from the live registry entirely.
- `chain_templates` - a node a shipped chain template has that the live
  installed copy of that template doesn't.

Translate each row's compact detail (`h1, h2 missing entirely` style) into one
line per hook or node, grouped under its own heading, so a human reads "these
three things are stale" rather than a string they have to parse themselves.

## What doctor cannot check

For every `kind: agent, ..., skill: X` binding in the live registry
(`~/.kraft/templates/registry.yaml`) where `X` contains a `:` - a reference
into this agent's own plugin system, not a file Kraft ships - check `X`
against the skills currently available to you (the list your own session
already has, via `using-superpowers`'s skill listing). `kraft/skill.py`
documents, on purpose, that the server side cannot answer this; you can,
because you are the agent that would have to load it.

List any `X` not in your available-skills list under its own "not installed
for this agent" heading. This is a per-agent fact, not a per-repo one — the
same registry can be fully served on one machine and missing skills on
another.

## If asked to fix something

Report-only until the human says to act. Then, for each item they pick:

1. State the exact edit before making it - which line in
   `~/.kraft/templates/registry.yaml` or the live chain template file, or
   which of `set_chain_template` / `set_node_overrides` / `set_agent_overrides`
   for a per-repo override instead of the shared file.
2. Wait for confirmation.
3. Apply it, then re-run `kraft admin doctor` and confirm the row that named
   the problem now reads clean. Report the failure plainly if it doesn't -
   don't declare it fixed on the strength of having made the edit.

A missing plugin skill has no edit to propose here - it is installed or it
isn't. Say so, and name what needs installing, rather than inventing a
workaround.
