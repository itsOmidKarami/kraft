---
name: check
description: "Reports where Kraft's config on this machine has drifted from what this Kraft version ships: chains that no longer resolve, a chain that hides Kraft's built-in kraft-default, a missing setup command, skills a chain names that are not installed. Use after `kraft admin update` has been applied, when `kraft admin templates lint` fails, a chain won't resolve, or a chain names a skill that isn't installed; a server that is unhealthy is kraft:doctor."
---

Kraft's tools come from the `kraft` MCP server. If `kraft` is not on PATH, this
plugin has been installed without the program it drives: say so and point at
https://kraft-sdlc.dev/get-started/install rather than reporting a
connection error.

# Checking Kraft's config

Run `kraft admin templates lint` and `kraft admin doctor`. Lint asks the
server: if it can't reach it, read doctor's `chains` row instead (it reads the
files with no server, but skips the `policy.yaml` check) and hand the server to
`kraft:doctor`. The server and MCP token rows are `kraft:doctor`'s. Any other
row that warns or fails and isn't listed here (`moved keys`, `harnesses.yaml`,
`profile:`, `add-ons`, ...): quote it verbatim with the fix its line names.
Then do the skills check below: neither command does it. If lint, every row
and that check come back clean, say so in one line and stop.

The config directory is `$KRAFT_HOME/config` (default `~/.kraft/config`).

- `lint` - every chain in the config directory's `chains/` that doesn't resolve
  against the library and the built-in `kraft-default` add-on, each with its
  reason: a reference to a component that isn't declared, a component extending
  one of another kind, a policy a chain widens past `policy.yaml`. It exits 1 on
  any error. A chain that doesn't resolve can't be filed, so this comes first.
- `kraft-default` (doctor) - Kraft's own chains and library are the built-in
  add-on `kraft-default`, read from the installed Kraft, so they cannot fall
  behind and there is no drift to compare. The row is always `ok`, and warns
  when it has something to say, one line each:
  - `chains/<file>.yaml is a full copy and hides kraft-default:<id>` - a home
    seeded before the built-in. It keeps its copy and Kraft never rewrites it;
    point at https://kraft-sdlc.dev/guides/run/upgrade-your-configuration and
    stop: it is a by-hand move that keeps the person's edits, so there is
    nothing for you to apply.
  - an update that was held, or one that changed `kraft-default` - the line
    says what; a held one is settled by an interactive `kraft admin update`.
  - an old GitHub copy of `kraft-default` - the line gives the two commands
    that move off it.
  - `harnesses.yaml lacks ...; using the packaged ones`: harmless, Kraft uses
    the packaged profiles; report it.
  - a repository id `kraft-default`: the person renames it in `repos.yaml`.
  - `maxima:` clamping built-in tasks: report it; the person raises the
    ceiling in `policy.yaml` or accepts it.
- `agent: <profile>` (doctor) - a harness profile some chain selects whose
  executable isn't on PATH, or which `harnesses.yaml` doesn't declare. The
  person installs the CLI; restarting Kraft afterwards is `kraft:doctor`'s job.
  A pass here is doctor's own PATH, not the service's.
- `capabilities` (doctor) - capabilities this Kraft has that the operator's
  seeded settings files predate. None ship today, so the row reads up to date.
  `config/` is seeded once and never overwritten, so when a release does ship
  one, read the row's `-> ` lines out as the edit each one needs: they are
  adoption instructions, not drift to be "fixed". Never offer to copy the
  shipped defaults over: a live library carries per-task `model`/`effort`
  choices the shipped defaults do not, and overwriting destroys them.
- `setup <repo>` (doctor) - a connected repo with no `setup_command` in
  `repos.yaml`. There is no default, so this repo's next work item stops when
  its worktree is built. The row carries the setup command `kraft repo connect`
  would propose today (from the repo's task runner, CI and lockfiles); check it
  against what the repo's own docs say, the same way step 1 of `kraft:onboard`
  checks a proposed `test_command`, then give it to the person to set in
  Settings › Repos. If it found none, ask whether the repo needs setup at all.

For the rows listed above, give one line per finding under its own heading,
so a human reads "these three things are stale" rather than a string they have
to parse. Any other row is quoted verbatim.

## What doctor cannot check

For every agent task in the live library and chains
(`library.yaml` and `chains/*.yaml` in the config directory) whose `skill:`
value contains a colon, first set aside what Kraft resolves itself:
`kraft:` (a method Kraft ships) and a namespace that `kraft admin addon list`
shows (an add-on's skill, read from its store; doctor and lint check those).
What is left is a reference into this agent's own plugin system. Check each
one against the skills currently available to you (the list your own session
already has), for the tasks whose agent profile runs this session's own harness
(`kraft admin harnesses` shows each profile's harness and the tasks that select
it). The Kraft server can't answer this, by design; you can, because you are
the agent that would have to load it. A task on another harness loads
its skills from that harness's install: list it as "cannot tell from here".
`kraft admin templates show ID --resolved` prints each task with the `skill:`
it inherited, so read that rather than the raw files.

List any value not in your available-skills list under its own "not installed
for this agent" heading. This is a per-agent fact, not a per-repo one - the
same library can be fully served on one machine and missing skills on another.

## If asked to fix something

Report-only until the human says to act. Then, for each item they pick:

1. State the exact edit before making it - which line in `library.yaml`, a
   chain file under `chains/` or `harnesses.yaml`. Commands in `repos.yaml` are
   the person's to set; tell them what to change. The per-item override tools
   (`set_chain`, `set_node_overrides`, `set_agent_overrides`) only keep one item
   clear of the drift and never clear a row: offer them only when the person
   asks for a one-item workaround.
2. Wait for confirmation.
3. Apply it, run `kraft admin reload` so the server reads the new library, then
   re-run `kraft admin templates lint` and `kraft admin doctor` and confirm the
   row that named the problem now reads clean. Report the failure plainly if it
   doesn't - don't declare it fixed on the strength of having made the edit.

A missing plugin skill has no edit to propose here - it is installed or it
isn't. Say so, and name what needs installing, rather than inventing a
workaround.
