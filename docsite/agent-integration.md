# Use it from an agent session

Kraft can also be driven from a coding-agent session over MCP, so work can be
filed, read, and unblocked without switching to the browser. This is the
faster path for onboarding a repo, checking a config for drift, or acting on
a gate — it's a slash command instead of remembering the CLI flags.

Two things get installed together: the `/kraft:*` skills, and the MCP server
whose tools they call (`create_work_item`, `approve_gate`, and so on). Either
the Claude Code plugin marketplace installs both, or `kraft admin init` writes
both by hand.

## Installing the plugin

Kraft publishes a [Claude Code plugin marketplace](https://docs.claude.com/en/docs/claude-code/plugin-marketplaces),
alongside Kraft Lite:

```
/plugin marketplace add itsOmidKarami/kraft
/plugin install kraft@kraft
```

This is the one-step path: the plugin manifest bundles `kraft admin mcp` as an
`mcpServers` entry, so installing it registers the MCP server *and* the
skills in the same step — as long as `kraft` itself is already on `PATH`
([Install](install.md)). It's managed, too: `/plugin update kraft` picks up
new skills instead of re-running init, and uninstalling is `/plugin
uninstall` instead of `rm -rf`. Kraft Lite's plugin doesn't bundle an MCP
server — see [Without the orchestrator](#without-the-orchestrator) below for
why it doesn't need one.

## Installing with `kraft admin init`

Reach for this instead of the plugin when you want the plain-file form (no
Claude Code plugin state to manage), the repo-scoped `.mcp.json` variant, or
you're registering the MCP server for a host other than Claude Code:

```bash
kraft admin init          # register the MCP server for your user, install the skills
kraft admin init --repo   # or write .mcp.json + .claude/skills/ into this repo
```

User scope shells out to `claude mcp add` rather than editing `~/.claude.json`
itself — that file is large, shared, agent-owned state. If `claude` is not on
`PATH`, `kraft admin init` prints the command for you to run instead of guessing.

Without `--repo` this registers the MCP server once for your whole user
account (every repo you open sees Kraft's tools, not just the one you ran it
from) — `--repo` scopes both the MCP registration and the skill files to the
current repo instead. Running it with no flag is the closest thing to a
global install today, same as the plugin.

This writes the skill files under `.claude/skills/kraft/` directly, with
nothing registered in Claude Code's managed plugin state — uninstalling is
`rm -rf` on the directory. Both forms produce the same `/kraft:*` commands.

## The skills

Each skill is one moment you'd reach for Kraft, not one skill per tool:

- **`/kraft:onboard`** — Connect a new repo: runs `repo connect`,
  `admin init --repo`, `admin doctor` in sequence, then checks each step
  actually matches the repo.
- **`/kraft:board`** — See what's running, what's blocked or waiting on a
  person, or the state of one work item.
- **`/kraft:prepare`** — Define a spec (and optionally a plan) for
  non-trivial work, then decide whether it runs inline in this session or
  hands off to Kraft.
- **`/kraft:handoff`** — File the work Kraft should run, after a spec and
  plan are agreed.
- **`/kraft:status`** — Report where a work item has got to: phase, next
  node, any gate it's waiting on.
- **`/kraft:gates`** — Approve or reject the gate a work item is waiting on,
  or pause/resume one heading the wrong way.
- **`/kraft:check`** — Check whether a repo's Kraft config has drifted from
  what this version ships: a hook stuck on a placeholder, a chain missing a
  node, a hook naming a skill that isn't installed. Report-only unless asked
  to fix.

Most call the same MCP tools listed below; `onboard` and `check` instead run
`kraft admin doctor`/`admin init` directly as shell commands. Either way,
`kraft` needs to be on `PATH`, and a skill that can't reach it (or the server)
says so rather than reporting a generic connection error.

The tools you'll reach for most yourself, over the same local HTTP API the
browser uses:

| | |
|---|---|
| read | `list_work_items`, `get_work_item`, `search` |
| write | `create_work_item`, `ensure_repo` |
| act | `approve_gate`, `reject_gate`, `pause_work_item`, `resume_work_item`, `retry_work_item` |

That's the everyday subset, not the full tool list — every `kraft` subcommand
(see the [CLI reference](cli.md)) has an MCP twin.

## Two rules enforced in code, not in prose

**An agent cannot start work.** Everything an agent creates lands paused, and the
board shows it as *Waiting to start* with a single Start button. Nothing spends
tokens until a person clicks it.

**A worker cannot act on itself.** Sessions Kraft starts carry
`KRAFT_WORK_ITEM_ID`, and any attempt to approve, reject, pause, resume, retry,
or abandon the work item running that session is refused before a request is
sent. A gate is where a human decides; an agent approving its own would make
the gate decorative.

`kraft admin mcp` runs the server on stdio, and every tool is also a `kraft`
subcommand, so hooks and non-MCP agents get the same surface — see the
[CLI reference](cli.md).

## Without the orchestrator

`plugins/kraft-lite/` runs the same chain inside a single agent session — same
node list, same gates, same caps, no service. It is the attended half of Kraft:
one chain, in front of you, resumable across sessions but not outliving your
terminal. Unlike the `kraft` plugin above, it needs neither the `kraft` binary
nor an MCP server — its skills shell out to a bundled `kl.py` (stdlib-only
Python) instead of calling tools. Install it from the same marketplace:

```
/plugin marketplace add itsOmidKarami/kraft
/plugin install kraft-lite@kraft
```

- **`/kraft-lite:init`** — Once per repo: detect the test command and which
  installed skills can serve each chain hook, write an editable registry.
- **`/kraft-lite:start`** — Materialize a chain's nodes and run the first one.
- **`/kraft-lite:next`** — Run the next node, or resume one after a gate, a
  new session, or a cleared context.
- **`/kraft-lite:gate`** — Present what a blocked gate needs decided, then
  record the approval or rejection.
- **`/kraft-lite:status`** — Report which node is live, what's blocking it,
  how many fix attempts are spent.

The packaged chain (`chains/default.json`) is fifteen nodes from spec through
plan, implementation, verification, and review, with four gates where a human
decides. State lives in `bd` when the repo has it, otherwise a plain
`.kraft-lite/chain.jsonl` — the same `bd export` format either way, so
adopting `bd` later is `bd import`, not a migration.

That directory is published as a standalone repo, so it must stay
self-contained: no import above `plugins/kraft-lite/`, no dependency beyond the
standard library. See
[`plugins/kraft-lite/README.md`](https://github.com/itsOmidKarami/kraft/blob/main/plugins/kraft-lite/README.md)
for what runs each node, chain freezing, and the rest of what Lite does and
doesn't do — that file is the source of truth and moves faster than this page.
