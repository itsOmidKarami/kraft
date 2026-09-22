# Agent Instructions

Build, test and run instructions for this repository. If `AGENTS.local.md`
exists beside this file, read it too — it holds the maintainer's private
issue-tracker workflow and is not part of the public repository.

## Build & Test

Everything goes through `just` — run `just` for the list.

```bash
just setup      # uv sync + npm install
just test       # backend tests (pass args: just test -k search)
just test-ui    # frontend unit tests
just lint       # ruff check + format check
```

## Running Kraft

Two ways, and neither is `python -m kraft` by hand.

```bash
just dev        # dev instance: state in .dev/, agents faked, UI on :5173
just dev-seed   # fill a running dev instance with work items in every state
just dev-reset  # throw .dev/ away

just install    # build the SPA, install the `kraft` command; then run `kraft`
```

`just dev` puts `fixtures/bin` (a `claude` symlink to `fixtures/fake-claude.sh`)
ahead of the real agent on PATH, so a dev instance never spends tokens. A work
item title containing `KRAFT_FAIL` or `KRAFT_SLOW` steers its own fake agent.

### The `kraft` command

Every MCP tool is also a subcommand, so a hook or a non-MCP agent gets the same
surface. `--json` on any verb prints the raw API payload.

```bash
kraft view list [--all] [--status=paused]   # the board, scoped to the cwd's repo
kraft view show [ID]                        # ID defaults to the worktree you are in
kraft item create "title" [--description "..."] [--spec P] [--plan P] [--auto-gate] [--autostart]  # files it paused unless --autostart
kraft item approve [ID] / kraft item reject [ID] --note "why"
kraft item pause [ID] / kraft item resume [ID] --steer "..."
kraft item retry [ID] [--steer "..."]       # the only door back onto a stopped item
kraft view search "query"
kraft view logs [ID] [-f] [-n N]            # a worker session's log; --json is NDJSON
kraft view events [ID] [--after N] [--type T]
kraft view watch                            # live board, needs a terminal
kraft view diff [ID] [--stat|--name-only]   # truncation and untracked always shown
kraft view docs [ID] / kraft view doc DOC_ID [--open [EDITOR]]
kraft repo list                             # `*` marks the repo you are in
kraft repo connect [PATH] / kraft repo disconnect [PATH]
kraft repo path [ID] (alias cd) / kraft repo open [ID]
kraft admin start [--host H] [--port P]      # same as bare `kraft`
kraft admin stop                             # SIGTERM to run/kraft.pid
kraft admin health                           # exit 1 when degraded
kraft admin doctor                           # every check at once; exit 1 on any
kraft admin reindex [--repo PATH]
kraft admin init [--repo] / kraft admin mcp  # register Kraft with an agent
```

Verbs live in four groups: `item` acts, `view` reads, `repo` is repositories and
their worktrees, `admin` is this machine's server. Typing an old flat verb
(`kraft list`) prints where it moved.

Installed Kraft keeps state in `$KRAFT_HOME` (default `~/.kraft`): `run/` for the
databases, logs and worktrees, `templates/` for the YAML the Settings screens
edit, seeded from the packaged defaults on first run and never overwritten after.

## Kraft Workers

A session with `$KRAFT_WORK_ITEM_ID` set is a Kraft worker, running in a
throwaway git worktree on its own branch. It commits everything it changes
before it exits — uncommitted work never reaches the merge request and is
destroyed with the worktree. This overrides the Conservative profile's
"do not run git commits" for commits only: a worker still does not push,
merge, sync Dolt, or close beads. Kraft does those itself.
