# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:6cd5cc61 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Agent Context Profiles

The managed Beads block is task-tracking guidance, not permission to override repository, user, or orchestrator instructions.

- **Conservative (default)**: Use `bd` for task tracking. Do not run git commits, git pushes, or Dolt remote sync unless explicitly asked. At handoff, report changed files, validation, and suggested next commands.
- **Minimal**: Keep tool instruction files as pointers to `bd prime`; use the same conservative git policy unless active instructions say otherwise.
- **Team-maintainer**: Only when the repository explicitly opts in, agents may close beads, run quality gates, commit, and push as part of session close. A current "do not commit" or "do not push" instruction still wins.

## Session Completion

This protocol applies when ending a Beads implementation workflow. It is subordinate to explicit user, repository, and orchestrator instructions.

1. **File issues for remaining work** - Create beads for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **Handle git/sync by active profile**:
   ```bash
   # Conservative/minimal/default: report status and proposed commands; wait for approval.
   git status

   # Team-maintainer opt-in only, unless current instructions forbid it:
   git pull --rebase
   git push
   git status
   ```
5. **Hand off** - Summarize changes, validation, issue status, and any blocked sync/commit/push step

**Critical rules:**
- Explicit user or orchestrator instructions override this Beads block.
- Do not commit or push without clear authority from the active profile or the current user request.
- If a required sync or push is blocked, stop and report the exact command and error.
<!-- END BEADS INTEGRATION -->


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
kraft list [--all] [--status=paused]   # the board, scoped to the cwd's repo
kraft show [ID]                        # ID defaults to the worktree you are in
kraft create "title"                   # files it paused; a human starts it
kraft approve [ID] / kraft reject [ID] --note "why"
kraft pause [ID] / kraft resume [ID] --steer "..."
kraft search "query"
kraft logs [ID] [-f] [-n N]            # a worker session's log; --json is NDJSON
kraft events [ID] [--after N] [--type T]
kraft watch                            # live board, needs a terminal
kraft diff [ID] [--stat|--name-only]   # truncation and untracked always shown
kraft docs [ID] / kraft doc DOC_ID [--open [EDITOR]]
kraft repos / kraft connect [PATH]     # `*` marks the repo you are in
kraft path [ID] (alias cd) / kraft open [ID]
kraft serve [--host H] [--port P]      # same as bare `kraft`
kraft health                           # exit 1 when degraded
kraft doctor                           # every check at once; exit 1 on any
kraft reindex [--repo PATH]
```

Installed Kraft keeps state in `$KRAFT_HOME` (default `~/.kraft`): `run/` for the
databases, logs and worktrees, `templates/` for the YAML the Settings screens
edit, seeded from the packaged defaults on first run and never overwritten after.
Design: `docs/superpowers/specs/2026-09-04-packaging-and-dev-execution-design.md`.

## Architecture Overview

_Add a brief overview of your project architecture_

## Conventions & Patterns

_Add your project-specific conventions here_
