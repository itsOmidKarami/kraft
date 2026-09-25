# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.



## Build & Test

Everything goes through `just` — run `just` for the list.

```bash
just setup      # uv sync + npm install
just test       # backend tests affected by your changes (testmon); --no-testmon for all
just test-ui    # frontend unit tests
just lint       # ruff check + format check
```

**Never call `pytest` / `uv run pytest` directly.** Always go through `just
test` (add `-k pattern` or a path to target specific tests; use
`--no-testmon` for a full run). Calling pytest raw skips testmon's
change-tracking and burns the full ~14min suite.

**Testing guideline:** see `docs/testing.md`. Two tiers (unmarked unit,
mocked at the adapter seam; `@pytest.mark.e2e("<cli>")` for a real CLI's
contract) and one rule for every pin: mutate the code it covers and confirm
that same test fails, or it isn't proof of anything. `just check-tests`
enforces what can be checked mechanically.

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
kraft item set-attachments [ID] [--spec P] [--plan P] [--drop KIND]  # revise a not-yet-started item's documents
kraft item approve [ID] / kraft item reject [ID] --note "why"
kraft item pause [ID] / kraft item resume [ID] --steer "..."
kraft item retry [ID] [--steer "..."]       # the only door back onto a stopped item
kraft item progress K [ID]                  # a worker saying it started plan task K
kraft view search "query"
kraft view logs [ID] [-f] [-n N]            # a worker session's log; --json is NDJSON
kraft view events [ID] [--after N] [--type T]
kraft view watch                            # live board, needs a terminal
kraft view diff [ID] [--stat|--name-only]   # truncation and untracked always shown
kraft view docs [ID] / kraft view doc DOC_ID [--open [EDITOR]]
kraft view artifact [ID]                    # the doc the pending gate is about
kraft repo list                             # `*` marks the repo you are in
kraft repo connect [PATH] / kraft repo disconnect [PATH]
kraft repo path [ID] (alias cd) / kraft repo open [ID]
kraft admin start [--host H] [--port P]      # same as bare `kraft`
kraft admin stop                             # SIGTERM to run/kraft.pid
kraft admin restart                          # stop, then start again the same way it was running
kraft admin health                           # exit 1 when degraded
kraft admin doctor                           # every check at once; exit 1 on any
kraft admin update [--restart] [-y] [--channel rc|beta|alpha]  # install the newest release; --restart also restarts
kraft admin reindex [--repo PATH]
kraft admin reload                           # reread the template library and policy.yaml from disk, no restart
kraft admin templates lint                   # check every chain in the library; exit 1 on any error
kraft admin templates show ID [--resolved]   # a chain file as written, or expanded
kraft admin init [--repo] / kraft admin mcp  # register Kraft with an agent
```

Verbs live in four groups: `item` acts, `view` reads, `repo` is repositories and
their worktrees, `admin` is this machine's server. Typing an old flat verb
(`kraft list`) prints where it moved.

Installed Kraft keeps state in `$KRAFT_HOME` (default `~/.kraft`): `run/` for the
databases, logs and worktrees, `templates/` for the YAML the Settings screens
edit, seeded from the packaged defaults on first run and never overwritten after.

## Architecture Overview

See [ARCHITECTURE.md](ARCHITECTURE.md).

## Conventions & Patterns

### Specs and plans are not committed

Design docs and implementation plans do not go into git. `.engineering/` and
`docs/superpowers/` are both gitignored (Kraft writes its own specs, plans,
review briefs and session notes there).

When a brainstorm produces a spec and a plan for Kraft, hand them over as work
item attachments instead:

```bash
kraft item create "title" --spec PATH --plan PATH
```

**An attachment is snapshotted at intake.** Intake copies the file into
`~/.kraft/run/attachments/<work-item-id>/`, and that copy, not the path you
passed, is what `builtins.ensure_worktree` copies into the worker's worktree
when the item runs. A stored copy that has gone missing fails the item before
its worktree is made, so it never runs with its gates trimmed and no document.

Editing the original after filing changes nothing on its own. To revise a spec
or plan before the item starts, re-attach it in place instead of abandoning and
re-filing:

```bash
kraft item set-attachments [ID] --spec PATH   # copied again; --plan likewise
kraft item set-attachments [ID] --drop spec   # removes it and puts its gate back
```

Once the item has started, its documents are fixed (the call answers 409): its
worktree already holds them, committed on its branch. `kraft item create` warns
when an open item in the same repo has the same title or implements a bead you
named; read that warning before filing twice.

### The test tree mirrors the source tree

A test for `src/kraft/<pkg>/<mod>.py` lives at `tests/<pkg>/test_<mod>.py` — not
`tests/test_<pkg>_<mod>.py`. Flat names under a hierarchical source is how the
layout drifted the first time. A module with no package mirrors nothing and
stays at `tests/test_<mod>.py`.

**Every `tests/` subdirectory needs an empty `__init__.py`.** This is
load-bearing, not tidiness. The mirrored tree has eleven duplicate basenames
(`test_gates.py` exists under `api/`, `executor/` and the root, and so on for
`test_db`, `test_auth`, `test_budget`, `test_escalate`, `test_progress`,
`test_reattach`, `test_repos`, `test_service`, `test_triggers`,
`test_work_items`), which pytest's default prepend import mode rejects as a
hard collection error. Packages fix it with no config change, and they keep
`tests/` on `sys.path` — which the 87 files doing `from support.harness import
...` depend on. Do not "simplify" this by switching to
`--import-mode=importlib`; that drops `tests/` off `sys.path` and breaks every
one of them.

Two things bite when you move or add a nested test:

- `Path(__file__)` paths are written relative to `tests/`, so a file one level
  down needs `parents[1]` where the root wanted `parents[0]`. Resolve them
  against the filesystem rather than trusting the arithmetic.
- Moving a test file breaks its `enforced-by:` pins in `docs/intent/`. CI runs
  `python -m kraft.intent`, which exits 1 on a broken pin, so repoint them in
  the same change and confirm with `just intent`.

### Marking a task already done in a reused plan

A plan attached to one work item is sometimes reused for a later item that
only implements one of its tasks — the rest already merged elsewhere. Say so
on the heading itself, not in prose the tooling can't read:

```
### Task 7: Suggest a setup command at connect time [DONE]
```

`kraft.progress.parse_tasks()` reads the `[DONE]` tag and keeps the board's
"Task N of M" from drifting onto a task that was never in scope for the
running work item.

## Kraft Workers

A session with `$KRAFT_WORK_ITEM_ID` set is a Kraft worker, running in a
throwaway git worktree on its own branch. It commits everything it changes
before it exits — uncommitted work never reaches the merge request and is
destroyed with the worktree. This overrides the Conservative profile's
"do not run git commits" for commits only: a worker still does not push,
merge, sync Dolt, or close beads. Kraft does those itself.

A worker's environment is built from an allowlist, not inherited from whatever
shell started the Kraft daemon: `PATH`, `HOME`, the usual locale and proxy
vars, Kraft's own `KRAFT_*`, and the agent's credential var. Anything else a
repo needs is declared in its `repos.yaml` entry — `env:` for literal values,
`env_passthrough:` to name a var the daemon already has. Do not assume a
variable from your own shell is present in a worktree.

@CLAUDE.local.md
