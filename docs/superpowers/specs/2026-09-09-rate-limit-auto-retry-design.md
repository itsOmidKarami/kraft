# Rate-limit auto-retry design

## Problem

The `claude` CLI, run under `--output-format stream-json`, emits a
`rate_limit_event` line mid-session when a launch is rejected for hitting a
usage window:

```json
{"type":"rate_limit_event","rate_limit_info":{"status":"rejected","resetsAt":1788968400,"rateLimitType":"five_hour", ...}}
```

The session then exits non-zero / `is_error: true`. Today Kraft has no way to
tell this apart from an ordinary agent failure: it reads as `failed`, runs
into `mark_needs_human`, and a person has to notice, wait for the window to
reset themselves, and click retry. The wait is mechanical — Kraft already
knows `resetsAt` — and should not need a human.

## Goals

- Detect a rejected `rate_limit_event` in an agent task's stream-json log.
- Stop the work item without treating it as a code failure (no fix loop, no
  `on_failure` repair task).
- Schedule the whole item to resume automatically once `resetsAt` passes.
- On resume, tell the agent it hit a limit, that the limit has since lifted,
  and to continue from where it left off.
- Cap the number of automatic retries, so an item stuck behind a permanent
  block (e.g. `overageDisabledReason: org_level_disabled`) eventually stops
  for a human instead of polling forever.

## Non-goals

- Per-task retry inside a running node (the whole work item reschedules,
  matching how a human already retries a stopped item).
- A configurable poll interval (fixed at 30s, matching `intake.py`'s floor).
- Any UI beyond a status label and a filter option — no live countdown.

## Detection

`subprocess.run_task` (`src/kraft/adapters/subprocess.py`) already tails the
session log and resolves a final status after the process exits. A new
best-effort helper, `_rate_limit_rejection(log_path) -> dict | None`, scans
every complete line in the log (not just the last one, unlike
`agent._envelope_is_error`, since `rate_limit_event` is not necessarily the
final line) for `type == "rate_limit_event"` with
`rate_limit_info.status == "rejected"`, and returns
`{"rate_limit_type": ..., "resets_at": <unix seconds>, "resets_at_iso": ...}`.
Malformed JSON or a missing field returns `None`, same failure posture as
`_envelope_is_error`.

`run_task` calls this right after `_resolve()` computes the base status, ahead
of `post_resolve` (which is agent.py's artifact-presence check — irrelevant
and wrong to run here, since a rejected launch produced no artifact by
construction):

```python
status = _resolve(result_path, returncode)
rate_limit = _rate_limit_rejection(log_path)
if rate_limit is not None:
    status = "rate_limited"
    await db.write(lambda c: events.append(
        c, work_item_id, "rate_limit_hit", {**rate_limit, "node_id": node_id}
    ))
elif post_resolve is not None:
    status = post_resolve(status, log_path, returncode)
```

`run_task` already closes over `db`, `work_item_id`, and `node_id`, so no new
parameters are needed here. This runs for every task kind (agent, subprocess,
forge); only an agent-kind log can ever contain the JSON shape, so the scan is
a harmless no-op for the others.

## Propagation

`"rate_limited"` is a new sentinel string, threaded through `executor.py`
exactly like the existing `"paused"` and `BUDGET` sentinels:

- `_measure_node`: if any task result is `"rate_limited"`, return it
  immediately — ranked above `BUDGET`/`"failed"` (a rate limit is not
  evidence of bad code) but below `"paused"` (a human's SIGTERM always wins).
- `_walk_node`: checked in both the plain-node branch and the fix-loop
  `while True:` branch, in each case immediately after `_measure_node`,
  alongside the existing `paused`/`BUDGET` checks. On `"rate_limited"`, calls
  `_stop_for_rate_limit` and returns its result — never reaches
  `on_failure`/fix-loop repair.

`_stop_for_rate_limit(db, work_item_id, node)` reads the most recently
appended `rate_limit_hit` event for this item (same reverse-scan idiom as
`_last_measurement`/`_needs_context_question`) to get `resets_at_iso`, then
calls `store.mark_rate_limited(work_item_id, node["id"], resets_at_iso)` and
returns `"rate_limited"` — a new outcome value `run`/`resume` pass straight
through, alongside `"needs_human"`/`"awaiting_gate"`/`"paused"`.

## Schema (migration → `SCHEMA_VERSION = 16`)

- `work_items.status` CHECK gains `'rate_limited'` — the existing 12-step
  rebuild used for the `'paused'` migration (SQLite cannot alter a CHECK in
  place).
- `work_items.retry_at TEXT` — nullable ISO-8601 timestamp; NULL except while
  `status = 'rate_limited'`.
- `worker_sessions.status` CHECK gains `'rate_limited'` — the session itself
  did not "fail" in the code sense, and the log view should say so.

`store.mark_rate_limited(conn, work_item_id, node_id, retry_at_iso)`:
UPDATE `work_items` (`status = 'rate_limited'`, `retry_at = retry_at_iso`),
append `work_item_rate_limited` event (`node_id`, `retry_at`). `api.py`'s
`_STOP_BOUNDARY` gains this event type too, so `_stop_reason` does not walk
past a live rate-limit stop to report a stale, already-superseded reason.

## Retry cap

Reuses the existing `retry_counters` table and `store.bump_counter`/`Cap`
machinery under a key `"rate_limit:<node_id>"` — per node, not per item: a
node that gets rate-limited, recovers, and completes must not spend the same
budget a *different* node's later rate limit then has to share. `Cap.wall_clock_s`
is meaningless here — the whole point is a potentially multi-hour wait — so
the cap is built with a large placeholder wall clock and only
`count > cap.attempts` is checked; a `# ponytail:` comment marks this at the
call site.

New `policy.yaml` key `rate_limit_retries: <int>`, default 5, parsed onto
`Policy.rate_limit_retries: int = 5`, validated as a positive int like the
other cap fields in `policy.py`.

Left uncleared on a successful automatic retry — like a fix-loop's own
counter, it is not reset just because the node it is watching made it back to
`active`, only when that node completes (the row is simply never touched
again) or a human retries. A manual `POST /work-items/{wid}/retry` already
calls `store.retry_after_cap`; that function is not touched for this key —
it is deliberately not cleared automatically, only ever by the same paths
that already clear a fix-loop counter.

## The poller

New module `kraft/rate_limit_retry.py`, mirroring `intake.py`'s `poller`/
`tick` shape exactly (fixed-interval `asyncio.sleep` loop,
`except asyncio.CancelledError: raise` / `except Exception: logger.exception`
around each tick so a bad tick never kills the poller). Always on — unlike
auto-intake this is not optional behaviour, so no `enabled` flag.

`tick(app)`: `SELECT id, repo, current_node_id, chain_definition FROM
work_items WHERE status = 'rate_limited' AND retry_at <= <now>`. For each due
row:

1. `bump_counter(work_item_id, "rate_limit:<node_id>", cap)`. Over cap →
   `store.mark_needs_human(work_item_id, node_id, "rate_limit retries
   exhausted after N attempt(s)")`, done.
2. Else: `store.retry_after_cap(work_item_id, node_id, None, RESUME_PROMPT)`
   — the exact call `POST /retry` makes for a node with no fix loop: flips
   `status` back to `'active'`, clears `retry_at` to `NULL` (Task 3 extends
   this function to do so), appends `work_item_retried` (`node_id`, `loop:
   null`, `steer`) — already one of `_STOP_BOUNDARY`'s events, so a stale
   `stop_reason` from before the rate limit cannot leak through. Then resolve
   `start_index` from `current_node_id` in the chain definition (same lookup
   `POST /retry` already does) and call `executor.run(db, run_dirs,
   work_item_id=wid, registry=..., start_index=..., policy=...,
   steer=RESUME_PROMPT, launch=_launch(st, row["repo"]))` — the same
   `_spawn`/`_guard` helpers `api.py` already exports.

Registered in `api.py`'s `lifespan`, next to `intake_task`: started
unconditionally, cancelled and awaited in the `finally` block alongside the
other background tasks.

## The resume prompt

```python
RESUME_PROMPT = (
    "You were working on this task when the agent hit an API rate limit and "
    "stopped. That limit has now reset — continue the work from where you "
    "left off."
)
```

Delivered as `steer` into `executor.run`, which is the same `Steer`/
`_steer_prefix` mechanism a human's `--steer` note already rides on — no new
prompt-injection path.

## Frontend

- `WorkItemStatus` (`frontend/src/types.ts`) gains `"rate_limited"`.
- `format.ts`'s status-label map gains an entry (`"rate limited"` or similar,
  matching the existing `needs_human` → "needs you" convention).
- `Board.tsx`'s status filter dropdown gains the option.

No countdown widget, no new detail-view section — the existing generic event
timeline already renders `work_item_rate_limited` like any other event once
`EventTimeline.tsx` has a label for it.

## Error handling

- A malformed/unreadable log during rate-limit detection: `None`, falls
  through to today's `post_resolve` behaviour — never worse than before this
  feature existed.
- A crash between `mark_rate_limited` and the next poller tick: the row is
  the state; the poller picks it up on the next tick after restart the same
  as it would have before the crash. No reattach-specific handling needed —
  a `rate_limited` item has no in-flight session to adopt.
- Cap breach: falls through to the existing `needs_human` card/gate UI,
  reusing `mark_needs_human` verbatim.

## Testing

- `_rate_limit_rejection`: unit tests with a fixture log built from the
  screenshot's exact line shape — one for a rejected event, one for an
  accepted/non-rejected `rate_limit_event` (must NOT trigger), one for no
  such line at all.
- `executor`: a rate-limited task stops the node without invoking
  `on_failure`/fix-loop, and without an artifact check running.
- `rate_limit_retry.tick`: a due row gets relaunched with the resume prompt;
  a row not yet due is left alone; a row over cap goes to `needs_human` and
  is not relaunched.
- Migration test: `SCHEMA_VERSION` 15 → 16 round-trips existing rows and
  accepts the new status/column.
