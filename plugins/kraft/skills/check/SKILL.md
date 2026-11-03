---
name: check
description: "Reports where a repo's Kraft config has drifted from what this Kraft version ships: chains that no longer resolve, a chain that hides Kraft's built-in kraft-default, settings-file capabilities the live config lacks, a missing setup command, skills a chain names that are not installed. Use after a Kraft upgrade or when chains or the library look stale; a server that is unhealthy is kraft:doctor."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# Checking a repo's Kraft config

Run `kraft admin templates lint` and `kraft admin doctor`. Between them they
carry the drift this skill exists to narrate. Doctor rows not listed here (the
server, the MCP token) are `kraft:doctor`'s; say so rather than diagnosing them.

- `lint` - every chain in `~/.kraft/config/chains/` that does not resolve
  against the library and the built-in `kraft-default` add-on, each with its reason: a reference to a component that
  isn't declared, a component extending one of another kind, a policy a chain
  widens past `policy.yaml`. It exits 1 on any error. A chain that does not
  resolve cannot be filed, so this comes first.
- `kraft-default` (doctor) - Kraft's own chains and library are the built-in
  add-on `kraft-default`, read from the installed Kraft, so they cannot fall
  behind and there is no drift to compare. The row is always `ok`, and warns
  when it has something to say, one line each:
  - `chains/<file>.yaml is a full copy and hides kraft-default:<id>` - a home
    seeded before the built-in. It keeps its copy and Kraft never rewrites it;
    point at the guide "Move onto kraft-default" and offer nothing more.
  - an update that was held, or one that changed `kraft-default` - the line
    says what; a held one is settled by an interactive `kraft admin update`.
  - an old GitHub copy of `kraft-default` - the line gives the two commands
    that move off it.
  - `harnesses.yaml lacks ...; using the packaged ones`, a repository id
    `kraft-default`, and `maxima:` clamping built-in tasks.
- `agent: <profile>` (doctor) - a harness profile some chain selects whose
  executable is not on PATH, or which `harnesses.yaml` does not declare.
- `capabilities` (doctor) - capabilities this Kraft has that the operator's
  seeded settings files predate (today the agent profiles in `harnesses.yaml`).
  `config/` is seeded once and never overwritten. Read the row's
  `-> ` lines out as the edit each one needs; they are adoption instructions,
  not drift to be "fixed". Never offer to copy the shipped defaults over: a
  live library carries per-task `model`/`effort` choices the shipped defaults
  do not, and overwriting destroys them.
- `setup <repo>` (doctor) - a connected repo with no `setup_command` in
  `repos.yaml`. There is no default, so this repo's next work item stops when
  its worktree is built. The row carries the setup command `kraft repo connect`
  would propose today (from the repo's task runner, CI and lockfiles); check it
  against what the repo's own docs say, the same way step 1 of `kraft:onboard`
  checks a proposed `test_command`, then give it to the person to set in
  Settings › Repos.

Translate each row's compact detail (`c1, c2 missing` style) into one line per
chain or node, grouped under its own heading, so a human reads "these three
things are stale" rather than a string they have to parse themselves.

## What doctor cannot check

For every agent task in the live library and chains
(`~/.kraft/config/library.yaml` and `chains/*.yaml`) whose `skill: X` names
a skill where `X` contains a `:`, first set aside what Kraft resolves itself:
`kraft:` (a method Kraft ships) and a namespace that `kraft admin addon list`
shows (an add-on's skill, read from its store; doctor and lint check those).
What is left is a reference into this agent's own plugin system. Check each
such `X` against the skills currently available to you (the list your own
session already has). The Kraft server cannot answer this, by design; you can,
because you are the agent that would have to load it.
`kraft admin templates show ID --resolved` prints each task with the `skill:`
it inherited, so read that rather than the raw files.

List any `X` not in your available-skills list under its own "not installed
for this agent" heading. This is a per-agent fact, not a per-repo one - the
same library can be fully served on one machine and missing skills on another.

## If asked to fix something

Report-only until the human says to act. Then, for each item they pick:

1. State the exact edit before making it - which line in
   `~/.kraft/config/library.yaml` or the live chain file under `chains/`,
   or which of `set_chain` / `set_node_overrides` /
   `set_agent_overrides` for a per-item override instead of the shared file.
2. Wait for confirmation.
3. Apply it, then re-run `kraft admin templates lint` and `kraft admin doctor`
   and confirm the row that named the problem now reads clean. Report the
   failure plainly if it doesn't - don't declare it fixed on the strength of
   having made the edit.

A missing plugin skill has no edit to propose here - it is installed or it
isn't. Say so, and name what needs installing, rather than inventing a
workaround.
