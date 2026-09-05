# Kraft

A local orchestrator for semi-autonomous software work. One FastAPI process plus a
React SPA: work items enter as **chains** — ordered nodes materialized from a YAML
template — and each node runs hook-point tasks through plugin adapters (a headless
agent, a subprocess, a builtin). Every retry loop is capped; hitting a cap escalates
to you with the full trace. Gates stop the chain where a human decision belongs.

Runs on your machine, binds loopback by default, and edits your repos through git
worktrees. Design docs: [`docs/consolidated/`](docs/consolidated/00_overview.md).

## Install and run

```bash
just setup      # uv sync + npm install
just install    # build the SPA, install the `kraft` command
kraft           # http://127.0.0.1:8765
```

State lives in `$KRAFT_HOME` (default `~/.kraft`):

| | |
|---|---|
| `~/.kraft/run/` | `orchestrator.db`, `index.db`, `logs/`, `results/`, `worktrees/` |
| `~/.kraft/templates/` | the YAML the Settings screens edit — chain templates, `registry.yaml`, `policy.yaml`, `repos.yaml`, `access.yaml` |

`templates/` is seeded from the packaged defaults on first run and never overwritten
after, so an upgrade cannot clobber an edited policy. It is a plain directory of
files on purpose: the Settings screens are an editor for something you can diff,
revert, and `git init` yourself.

Binding off loopback requires a password — set one in Settings → Access while still
on `127.0.0.1`. The process refuses to start on a LAN address without one.

`KRAFT_HOME=~/kraft-other kraft` gives you a second, fully separate instance.

## Use it from an agent session

Kraft can also be driven from a coding-agent session over MCP, so work can be
filed, read, and unblocked without switching to the browser.

```bash
kraft init          # register the MCP server for your user, install the skills
kraft init --repo   # or write .mcp.json + .claude/skills/ into this repo
```

User scope shells out to `claude mcp add` rather than editing `~/.claude.json`
itself — that file is large, shared, agent-owned state. If `claude` is not on
`PATH`, `kraft init` prints the command for you to run instead of guessing.

The skills install as a plugin, so they namespace: `/kraft:handoff` to file work
after a spec and plan are agreed, `/kraft:board` to see what is running or
blocked, `/kraft:gates` to approve, reject, or steer. That is a plain directory
tree under `.claude/skills/kraft/` with a `.claude-plugin/plugin.json` — nothing
is registered in Claude Code's managed state, and uninstalling is `rm -rf` on the
directory. It works the same at user and repo scope.

Nine tools, over the same local HTTP API the browser uses:

| | |
|---|---|
| read | `list_work_items`, `get_work_item`, `search` |
| write | `create_work_item`, `ensure_repo` |
| act | `approve_gate`, `reject_gate`, `pause_work_item`, `resume_work_item` |

Two rules are enforced in code, not in prose:

**An agent cannot start work.** Everything an agent creates lands paused, and the
board shows it as *Waiting to start* with a single Start button. Nothing spends
tokens until a person clicks it.

**A worker cannot act on itself.** Sessions Kraft starts carry
`KRAFT_WORK_ITEM_ID`, and any attempt to approve, reject, pause, or resume the
work item running that session is refused before a request is sent. A gate is
where a human decides; an agent approving its own would make the gate decorative.

`kraft mcp` runs the server on stdio, and every tool is also a `kraft` subcommand,
so hooks and non-MCP agents get the same surface. Design:
[`docs/superpowers/specs/2026-09-05-agent-integration-design.md`](docs/superpowers/specs/2026-09-05-agent-integration-design.md).

## Develop

```bash
just dev        # backend + vite, state in .dev/, agents faked — UI on :5173
just dev-seed   # fill a running dev instance with work items in every state
just dev-reset  # throw .dev/ away
```

`just dev` puts `fixtures/bin` on `PATH` ahead of the real agent, where `claude` is a
symlink to [`fixtures/fake-claude.sh`](fixtures/fake-claude.sh) — the same fake the
test suite uses, so it cannot rot. A dev instance never spends tokens and never
touches `~/.kraft`.

`dev-seed` drives the real HTTP API rather than writing rows, so the data is whatever
the executor actually produces. It lands four work items in four states: completed, a
pending gate, failed, and paused mid-flight. A title containing `KRAFT_FAIL` or
`KRAFT_SLOW` steers that item's fake agent without affecting the others.

```bash
just test       # backend tests (args pass through: just test -k search)
just test-ui    # frontend unit tests
just e2e        # Playwright (see frontend/e2e/README.md)
just lint       # ruff check + format check
just fix        # autofix
```

Everything is `just` — run `just` for the full list.

## Layout

```
src/kraft/        orchestrator: api, executor, policy, store, adapters/, index/
frontend/         React SPA (vite)
templates/        default chain templates + registry/policy — the install seed
dev/seed.py       dev-instance seeder
fixtures/         fake agent + the PATH shim just dev uses
docs/consolidated/  the design this implements
```

## Requirements

Python 3.14+, [uv](https://docs.astral.sh/uv/), Node 20+, git,
[`bd`](https://github.com/gastownhall/beads) for work-graph intake, and `claude` for
real runs (not needed for `just dev`). Semantic search is opt-in:
`just setup-vector` (downloads a ~130MB model on first search); without it `/search`
still works in FTS mode.
