# Architecture

Kraft is one FastAPI process and a React SPA, running on your machine. It takes
a unit of work, runs it through an ordered series of steps, and stops to ask you
whenever a decision belongs to a person.

## The model

A **work item** is one unit of work and produces one merge request. It enters as
a **chain**: an ordered list of **nodes** materialized from a YAML template in
`templates/`. Each node names one or more **hook points** — `on.test.run`,
`on.mr.open`, `on.review.local.run` — and each hook point is bound to an
**adapter** by `templates/registry.yaml`.

There are three kinds of adapter, in `src/kraft/adapters/`:

- **agent** — runs a headless coding agent in a git worktree.
- **subprocess** — runs a command.
- **builtin** (`src/kraft/builtins.py`) — work Kraft does itself: preparing a
  worktree, copying attachments, opening a merge request through `gh` or `glab`
  (`adapters/forge/`).

Two things make a chain stop:

- A **gate** — a node declares `gate_after`, the chain halts, and the work item
  becomes `needs_human`. You approve, reject with a note, or steer.
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
