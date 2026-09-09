# Kraft

A local orchestrator for semi-autonomous software work. One FastAPI process plus a
React SPA: work items enter as **chains** — ordered nodes materialized from a YAML
template — and each node runs hook-point tasks through plugin adapters (a headless
agent, a subprocess, a builtin). Every retry loop is capped; hitting a cap escalates
to you with the full trace. Gates stop the chain where a human decision belongs.

Runs on your machine, binds loopback by default, and edits your repos through git
worktrees. Design docs: [`docs/consolidated/`](docs/consolidated/00_overview.md).

## Install and run

No clone needed. This fetches the wheel from the newest tagged release, and
installs `uv` first if you do not have it:

```bash
curl -fsSL https://gitlab.com/itsOmidKarami/kraft/-/raw/main/install.sh | sh
kraft admin init   # register the MCP server and skills with your agent
kraft              # http://127.0.0.1:8765
```

[`install.sh`](install.sh) is short and worth reading before you pipe it to a
shell. `kraft admin update` installs the newest release later on, and
`kraft --version` says what you have.

### From source (development)

```bash
just setup      # uv sync + npm install
just install    # build the SPA, install the `kraft` command
kraft           # http://127.0.0.1:8765
```

Releasing, and the labels a merge request needs: [CONTRIBUTING.md](CONTRIBUTING.md).

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
kraft admin init          # register the MCP server for your user, install the skills
kraft admin init --repo   # or write .mcp.json + .claude/skills/ into this repo
```

User scope shells out to `claude mcp add` rather than editing `~/.claude.json`
itself — that file is large, shared, agent-owned state. If `claude` is not on
`PATH`, `kraft admin init` prints the command for you to run instead of guessing.

They also publish as a marketplace plugin, alongside Kraft Lite:
`/plugin marketplace add itsOmidKarami/kraft`.

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

`kraft admin mcp` runs the server on stdio, and every tool is also a `kraft` subcommand,
so hooks and non-MCP agents get the same surface. Design:
[`docs/superpowers/specs/2026-09-05-agent-integration-design.md`](docs/superpowers/specs/2026-09-05-agent-integration-design.md).

## Without the orchestrator

`plugins/kraft-lite/` runs the same chain inside a single agent session — same
node list, same gates, same caps, no service. It is the attended half of Kraft:
one chain, in front of you, resumable across sessions but not outliving your
terminal. Chain and policy come from `templates/`, rendered by `just lite-build`.

That directory is published as a standalone repo by `just lite-publish`, so it
must stay self-contained: no import above `plugins/kraft-lite/`, no dependency
beyond the standard library. `dev/build_lite_chain.py` and
`tests/kraft_lite_artifact_test.py` are the two pieces that deliberately live
outside it, because they are the seam between the two repos.

See [`plugins/kraft-lite/README.md`](plugins/kraft-lite/README.md).

## The `kraft` command

`kraft` with no arguments serves. Subcommands talk to a running server.

```bash
kraft view list                      # the board, scoped to the repo you are in
kraft view list --all --status=paused
kraft view show                      # the work item whose worktree you are in
kraft item create "fix the flaky test" --description "..."   # files it paused; a human starts it
kraft item approve                   # approve whichever gate is pending
kraft item reject --note "the plan skips migrations"
kraft item pause / kraft item resume --steer "try the other adapter"
kraft item retry                     # re-run the node a stopped item stopped on
kraft view search "retry policy"
```

Every verb takes `--json`, which prints the raw API payload — the same value
`kraft admin mcp` hands an agent. An id is optional wherever the work item can be
inferred from the directory you are standing in.

Following a running item:

```bash
kraft view logs -f            # the current session's log, until it stops
kraft view logs --session <id> -n 0
kraft view events             # node transitions, gate decisions, escalations
kraft view watch              # a live board, redrawn on every event
```

`kraft view logs --json` emits NDJSON — one object per line — because a stream has no
end to close an array on.

Reviewing before you approve:

```bash
kraft view diff --stat        # how big is it
kraft view diff --name-only   # changed and untracked paths
kraft view diff               # the coloured body, through $PAGER
kraft view docs               # specs, plans and summaries linked to the item
kraft view doc <id> --open    # open one in an editor on the server's machine
```

A truncated diff always says so on its last line, and files the agent wrote
without `git add` are listed separately — they are invisible in a unified diff.

Repos and worktrees:

```bash
kraft repo list              # what is connected; `*` marks the one you are in
kraft repo connect            # connect the current repo (safe to repeat)
kraft repo disconnect         # forget it again; work items are untouched
cd "$(kraft repo path <id>)"  # into the item's worktree; `kraft repo cd` is an alias
kraft repo path --shell       # a shell function that does the cd for you
kraft repo open <id>          # the worktree in an editor
```

Editing a repo's settings stays in the UI.

Service and admin:

```bash
kraft admin start --port 9000  # the same as bare `kraft`; flag > env > access.yaml
kraft admin stop               # SIGTERM to the pid in run/kraft.pid
kraft admin health             # exit 1 when degraded, reasons on stdout
kraft admin doctor             # every check in one pass; exit 1 if any fails
kraft admin reindex [--repo P] # rescan documents into the search index
```

A non-loopback bind still refuses to start without a password, flag or not.
The server runs in the foreground, so Ctrl-C stops the one in front of you;
`kraft admin stop` is for the one you started somewhere else. A second start
against the same run directory is refused while the first is alive.

The verbs live in four groups — `item` acts, `view` reads, `repo` is
repositories and their worktrees, `admin` is this machine's server. Typing an
old flat verb prints where it moved.

### Shell completion

`kraft` ships tab completion for zsh (and any other shell `argcomplete`
supports) via [`argcomplete`](https://github.com/kislyuk/argcomplete). Add
one line to `~/.zshrc`:

```zsh
eval "$(register-python-argcomplete kraft)"
```

then `kraft it<TAB>` completes to `kraft item`, `kraft item <TAB>` lists
`create approve reject pause resume retry abandon`, and so on down the verb
tree. Takes effect after your next `kraft` install or `uv sync`.

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

Python 3.14+, [uv](https://docs.astral.sh/uv/), Node 20+, git, and `claude` for
real runs (not needed for `just dev`).
[`bd`](https://github.com/gastownhall/beads) is optional: with it, every work
item gets a tracked bead, and without it Kraft files work anyway and says so.
Semantic search is opt-in:
`just setup-vector` (downloads a ~130MB model on first search); without it `/search`
still works in FTS mode.
