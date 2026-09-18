<!-- Mirrors ARCHITECTURE.md at the repo root; keep both in sync. -->

# Architecture

Kraft is one FastAPI process and a React SPA, running on your machine. It takes
a unit of work, runs it through an ordered series of steps, and stops to ask you
whenever a decision belongs to a person.

For the vocabulary this page uses — work item, chain, node, hook point, adapter,
gate, cap — see [Concepts](concepts.md).

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
overwritten after, so an upgrade cannot clobber an edited policy. See
[Configuration](configuration.md) for what's in those files.

## Intended behaviour, written down separately

`docs/intent/` states what the system is supposed to do, as numbered
requirements, each with an `enforced-by:` line naming the test that pins it.
`uv run python -m kraft.intent` checks that every pinned test still exists —
which is how a renamed test stops silently unpinning a requirement.
