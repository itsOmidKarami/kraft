# Escalate to Kraft-Agent — design

Date: 2026-09-10

## Problem

A work item stuck at `needs_human` today has two doors: `retry` (relaunch the
same node, optionally with a `--steer` note) and `pause`/`resume`. Both are
one-shot — no back-and-forth, no memory of what was already tried. Some
`needs_human` stops need a human and an agent going back and forth — inspect,
try something, report, take direction, try again — the way a human partner
and Claude work through a stuck problem in an interactive session. There is
no such door onto a work item today.

## Goal

`kraft item escalate <id> --message "..."` opens (or continues) a headless,
resumable Claude session scoped to that work item's worktree. The human sends
a message, the agent works, reports back. Later messages resume the *same*
underlying CLI session, so context — what it already tried, what it already
ruled out — carries forward. If the item's status changes between
escalations, the agent is told the current state on every turn, so it never
acts on stale information. The agent may resolve the stop itself: it has the
same standing as a human sitting in that worktree, so it can run
`kraft item retry` itself once it believes the fix is good, or it can stop
and hand back for a human to decide.

## Non-goals

- Not a replacement for `retry`/`pause`/`resume`/steer notes — those still
  exist, for the cases a one-shot relaunch already covers.
- Not available outside `needs_human`. A work item that is `active`, `paused`,
  or `completed` has no escalate door; the existing verbs already cover those.
- Not a new UI surface beyond the existing `kraft view logs`/`events` — no new
  live chat viewer.
- Not a general "give an agent free rein over the host" feature — it is
  scoped to the one work item's one worktree, same as every other Kraft
  agent dispatch.

## Architecture

Escalation turns are dispatched through the exact machinery every chain node
already uses (`_subprocess.run_task` via `adapters.agent.run_agent_task`),
with two differences from an ordinary chain dispatch:

1. **Resumable.** Once a work item has escalated once, Kraft holds onto the
   CLI's own session id (`claude`'s `--resume`/`-r` identity, distinct from
   Kraft's own `worker_sessions.id` uuids) and passes `--resume <id>` on every
   later turn, plus `--autocompact auto` so the CLI keeps that resumed
   transcript bounded on its own — no Kraft-side summarize/compact code.

2. **Not a worker.** A chain dispatch sets `KRAFT_WORK_ITEM_ID` in the agent's
   env, which is how `client.resolve_context()` marks it `origin="worker"`
   and `client._forbid_self_action` refuses to let it act on its own item
   (design §6 rule 2 — a worker cannot approve its own gate). An escalation
   dispatch deliberately omits that env var. `resolve_context()`'s existing
   cwd-based fallback then resolves it as `origin="user"` (its cwd *is*
   `run_dirs.worktrees/<work_item_id>`), exactly as if a human had typed the
   command from that worktree. No change to the guard: an escalation agent
   gets to call `kraft item retry` because it *is not* a worker, by the
   guard's own existing definition — not because the guard grew an
   exception.

Everything else — pid tracking, log streaming, crash recovery via
`reattach.py`, usage/cost accounting — is the existing `worker_sessions`
machinery, unmodified. `reattach()` scans all `pending`/`running` sessions by
status regardless of `node_id`, so an escalation session that crashes
mid-turn is picked up the same way any chain session is.

## Data flow

```
human                    kraft server                      escalation agent
  |  POST /escalate           |                                    |
  |--------------------------->|  409 if not needs_human            |
  |                            |  409 if a turn is already running  |
  |<--- 202, session id -------|                                    |
  |                            |  schedule background dispatch      |
  |                            |------------------------------------>|
  |                            |   worker_sessions row created        |
  |                            |   (hook_point=escalation,            |
  |                            |    node_id=current_node_id)          |
  |                            |                                    | works in worktree,
  |                            |                                    | writes result JSON +
  |                            |                                    | .engineering/sessions/{id}.md
  |                            |<------------------------------------|
  |                            |  session_exited: parse CLI session   |
  |                            |  id from log, save to work_items     |
  |  kraft view logs -f        |                                    |
  |<---------------------------|                                    |
  |  POST /escalate (2nd msg) -|  --resume <saved id>, --autocompact  |
  |                            |------------------------------------>|
```

## Components

### Schema (migration, `SCHEMA_VERSION` 18 → 19)

```sql
ALTER TABLE work_items ADD COLUMN escalation_session_id TEXT;
```

The CLI-native session id. `NULL` until the first turn. One column, not a
new table — it is 1:1 with a work item, same shape as the existing
`pending_steer_context` column.

No change to `worker_sessions`'s schema. Escalation turns are ordinary rows:
`hook_point = "escalation"`, `node_id = <work item's current_node_id at
dispatch time>` — meaningful here, since the current node *is* the stuck
node, not a sentinel. `status` uses the existing enum (`done`,
`done_with_concerns`, `failed`, `needs_context`, ...) unchanged.

### Result contract addition

The existing worker result-file contract (`status`, `concerns`, `question`,
`session_summary_ref` — see `adapters/agent.py::_CTX`) gains one optional
field for escalation turns only:

- `reply` (string) — the message to show the human. Reusing `concerns` or
  `question` for this would work mechanically but reads wrong in the event
  log ("concerns" implying doubt, "question" implying a stop) for what is
  usually just "here's what I did." `read_result_fields` (`adapters/
  subprocess.py`) grows one more optional key, same pattern as the existing
  four.

### `src/kraft/escalate.py` (new)

```python
async def dispatch(db, run_dirs, work_item_id, message, launch) -> str
```

- Reads the work item row fresh (status, current node, latest
  `needs_human` reason via the existing event-log scan pattern used by
  `_needs_context_question`/`_last_measurement`).
- Builds the prompt: a state-snapshot block (regenerated every call — no
  diffing against a prior snapshot; today's live state is always correct to
  hand over, which is the simplest way to guarantee the "no stale
  information" requirement) + the human's message + instructions (full repo
  access in this worktree; write `.engineering/sessions/{id}.md` on the way
  out, same convention as any Kraft session; if the issue is resolved, run
  `kraft item retry <work_item_id>` yourself — you are not a Kraft worker,
  you may).
- Calls `_agent.run_agent_task` with two new, narrowly-scoped parameters:
  - `resume_session_id: str | None` — when set, adds `--resume <id>
    --autocompact auto` to argv (new `Profile` fields `resume` and
    `autocompact`, same pattern as every other CLI-spelling field).
  - `identify_as_worker: bool = True` — `run_agent_task` only sets
    `KRAFT_WORK_ITEM_ID`/`KRAFT_SESSION_ID` in the child env when this is
    true. Every existing call site keeps the default; `escalate.dispatch`
    passes `False`.
- On exit, reads the first stream-json line of the session's log
  (`{"type": "system", "subtype": "init", "session_id": ...}`) and, if
  present, writes it to `work_items.escalation_session_id`. Always
  overwrites with whatever the run reported — covers the CLI's own "starts a
  copy and says so" fallback case (`claude --help`) without Kraft needing to
  detect it specially.

### API / CLI / MCP surface

- `POST /work-items/{id}/escalate` — body `{"message": str}`.
  - 409 if `work_items.status != 'needs_human'`.
  - 409 if the item's latest `hook_point='escalation'` worker_sessions row
    is `pending`/`running` (one turn at a time, same as the CLI's own
    `--resume` semantics — a second concurrent `-p --resume <id>` would
    race the same session).
  - Otherwise schedules `escalate.dispatch` as a background task (same
    scheduling path `run`/`resume` already use) and returns 202 with the new
    `worker_sessions.id`.
- `kraft item escalate [ID] --message "..."` — thin CLI wrapper, same shape
  as `kraft item retry [ID] [--steer ...]`.
- MCP tool `escalate_work_item(work_item_id, message)` — same
  docstring-as-description convention as every tool in `mcp.py`.
- No new read surface: `kraft view logs [ID] -f` and `kraft view events [ID]`
  already tail any worker session by `work_item_id`; an escalation session
  is just one more row they already show.

## Error handling

- **Concurrent escalate calls**: rejected with 409 (see above) rather than
  queued — a human waiting on a reply who sends a second message before the
  first returns should see a clear rejection, not a silently queued second
  prompt racing the first.
- **Crash mid-turn**: identical to any worker session — `reattach()` on
  restart either re-adopts the live pid or, finding neither a live pid nor a
  result file, calls `store.mark_needs_human` (a no-op status-wise, since the
  item is already `needs_human`; it does add an event explaining the
  crash — same value it already has for a crashed chain node).
- **Work item leaves `needs_human` some other way** (e.g. a human runs
  `kraft item retry` directly, bypassing escalate) while an escalation
  session exists: the *next* escalate call still 409s correctly (status
  check), and the persisted `escalation_session_id` is simply reused
  whenever the item next lands on `needs_human` and someone escalates again.
  Nothing to clean up.
- **`bd`/repo/git failures inside the escalation agent**: the agent's own
  problem to report through `reply`/`concerns`, same as any worker — no
  special-casing in `escalate.dispatch`.

## Testing

No framework beyond what `tests/` already uses (plain `pytest`, direct
calls into the modules under test):

- `escalate.dispatch`: prompt contains the live state snapshot and the
  human's message; `resume_session_id` set → argv contains `--resume <id>
  --autocompact auto`; unset → argv contains neither; `identify_as_worker`
  never sets `KRAFT_WORK_ITEM_ID` for an escalation dispatch.
- CLI-session-id capture: given a fake log file whose first line is a
  stream-json init event, `escalate.dispatch` persists that id to
  `work_items.escalation_session_id`; given a log with no such line, the
  column is left untouched.
- `store`: migration 19 adds the column; existing rows read back with
  `escalation_session_id IS NULL`.
- API: `POST /escalate` on a non-`needs_human` item → 409; on an item with a
  `running` escalation row → 409; otherwise schedules and returns 202.
- One CLI and one MCP smoke test, matching the existing style for other
  `item` verbs.

## Open items for the implementation plan

- Exact wording of the state-snapshot and instruction prompt text (mirrors
  `_CTX`/`_STEER_PROMPT` in `adapters/agent.py`; left to the plan/TDD cycle
  rather than fixed here).
- Whether `kraft view show <id>` should surface the latest escalation
  `reply` inline, or whether pointing at `kraft view logs` is enough —
  small, decide during implementation.
