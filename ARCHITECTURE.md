# Architecture

Kraft is one FastAPI process and a React SPA, running on your machine. It takes
a unit of work, runs it through an ordered series of steps, and stops to ask you
whenever a decision belongs to a person.

## The model

A **work item** is one unit of work and produces one merge request. It enters as
a **chain**: an ordered list of **nodes** resolved from one file under
`templates/chains/` against the reusable components in `templates/library.yaml`
(Template Schema V1, `src/kraft/templates/`), and frozen onto the item at
intake. An execution node runs typed **tasks**, of four kinds:

- **agent** (`src/kraft/adapters/`) — runs a headless coding agent, on the
  harness profile (`harnesses.yaml`: `claude`, `codex`, `gemini`, ...) the task
  names, in a git worktree.
- **subprocess** (`src/kraft/adapters/`) — runs a command.
- **builtin** (`src/kraft/builtins.py`) — work Kraft does itself, such as
  running the repo's changed test scopes.
- **forge** (`src/kraft/adapters/forge/`) — talks to GitHub or GitLab: opening
  a merge request, waiting on CI, marking it ready, merging.

Two things make a chain stop:

- A **gate** — a node of its own (`kind: gate`); the chain halts there and the
  work item becomes `needs_human`. You approve, reject with a note, or steer.
- A **cap** — every retry loop is bounded. Hitting the bound escalates to you
  with the full trace rather than looping.

## The process

| Area | Where |
|---|---|
| HTTP API and WebSocket | `src/kraft/api/`, `ws.py` |
| Chain execution | `src/kraft/executor/` |
| Persistence (SQLite) | `src/kraft/store/`, `db.py` |
| Retry, escalation and budget rules | `policy.py`, `escalate.py`, `rate_limit_retry.py` |
| Full-text and vector search | `src/kraft/index/` |
| CLI (every MCP tool is also a subcommand) | `src/kraft/cli/` |
| MCP server | `mcp.py` |
| Agent skills shipped in the wheel | `src/kraft/skills/`, `plugins/kraft/skills/` |
| SPA | `frontend/src/` |

State lives in `$KRAFT_HOME` (default `~/.kraft`): `run/` holds the databases,
logs, results and worktrees; `templates/` holds the YAML the Settings screens
edit. `templates/` is seeded from the packaged defaults on first run and never
overwritten after, so an upgrade cannot clobber an edited policy.

## Intended behaviour, written down separately

`docs/intent/` states what the system is supposed to do, as numbered
requirements, each with an `enforced-by:` line naming the test that pins it.
`uv run python -m kraft.intent` checks that every pinned test still exists —
which is how a renamed test stops silently unpinning a requirement.
