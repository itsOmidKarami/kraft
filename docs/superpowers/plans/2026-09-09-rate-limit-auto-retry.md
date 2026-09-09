# Rate-limit auto-retry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When an agent's launch is rejected by the `claude` CLI's rate limiter, stop the work item without treating it as a code failure, and have Kraft relaunch it automatically once the limit resets, telling the agent to continue where it left off.

**Architecture:** A new best-effort log scan (`subprocess._rate_limit_rejection`) turns a rejected `rate_limit_event` line into a `"rate_limited"` status, which flows through `executor.py` exactly like the existing `"paused"`/`BUDGET` sentinels and lands the item on a new `rate_limited` work-item status with a `retry_at` timestamp. A new always-on background poller (`kraft/rate_limit_retry.py`, the same shape as `kraft/intake.py`'s) relaunches any due item through the exact code path `POST /work-items/{id}/retry` already uses, carrying a fixed resume prompt as the steer text. A capped counter (reusing `retry_counters`) stops the cycle and falls back to `needs_human` if the same node keeps getting rate-limited.

**Tech Stack:** Python 3.14 / FastAPI / SQLite (backend), TypeScript / React / Vitest (frontend). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-09-rate-limit-auto-retry-design.md`

## Global Constraints

- No new third-party dependency, frontend or backend.
- The poller has no `enabled` flag — always on, unlike auto-intake (spec "The poller").
- The poller ticks on a fixed 30s interval — no config knob (spec "Non-goals").
- `Cap.wall_clock_s` is not meaningful for the rate-limit counter; only `count > cap.attempts` is ever checked against it (spec "Retry cap").
- `rate_limit_retries` defaults to `5` when unset in `policy.yaml` and when `app.state.policy` is `None` entirely.
- Detection keys on `rate_limit_info.status == "rejected"` at the top level, never `overageStatus` (confirmed against a real log: `overageStatus` can read `"rejected"` on an otherwise `status: "allowed"` line, which must NOT trigger).

---

## Task 1: Schema — `rate_limited` status and `retry_at` column

**Files:**
- Modify: `src/kraft/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `work_items.status` CHECK accepts `'rate_limited'`; `work_items.retry_at TEXT` (nullable); `worker_sessions.status` CHECK accepts `'rate_limited'`; `db.SCHEMA_VERSION == 16`.

- [ ] **Step 1: Write the failing migration test**

Add to `tests/test_db.py`, after `test_migrating_v13_adds_the_branch_column`:

```python
def test_migrate_v15_to_v16_rebuilds_for_rate_limited(tmp_path):
    """v15's CHECKs have no 'rate_limited' and work_items has no retry_at;
    SQLite cannot alter a constraint, so both tables are rebuilt the same
    12-step way migration 4 and 9 used."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        15,
        drop_lines=("retry_at         TEXT,", "-- set while status = 'rate_limited'"),
        replace=(
            (
                "'rate_limited')),",
                "                     ('active', 'needs_human', 'completed', 'paused', "
                "'abandoned')),",
            ),
            (
                "'done_with_concerns', 'needs_context', 'rate_limited')),",
                "                    'done_with_concerns', 'needs_context')),",
            ),
        ),
    )
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, created_at) VALUES ('s1', 'w1', 'verify', 'on.test.run', "
        "'/l', '/r', 'pending', 'now')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE work_items SET status = 'rate_limited' WHERE id = 'w1'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE worker_sessions SET status = 'rate_limited' WHERE id = 's1'")
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn2.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1
    assert conn2.execute("SELECT count(*) FROM worker_sessions").fetchone()[0] == 1

    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(work_items)")}
    assert "retry_at" in cols
    row = conn2.execute("SELECT retry_at FROM work_items WHERE id = 'w1'").fetchone()
    assert row["retry_at"] is None  # pre-existing row: NULL, not "not rate limited yet"

    conn2.execute("UPDATE work_items SET status = 'rate_limited', retry_at = 'x' WHERE id = 'w1'")
    conn2.execute("UPDATE worker_sessions SET status = 'rate_limited' WHERE id = 's1'")
    assert conn2.execute("PRAGMA foreign_key_check").fetchall() == []
    index_names = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_worker_sessions_status" in index_names


def test_fresh_schema_has_retry_at(tmp_path):
    """SCHEMA_SQL and the migration path must agree — a fresh install and an
    upgraded one are the same database."""
    conn = db._connect(tmp_path / "fresh.db")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "retry_at" in cols
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'rate_limited','now','now')"
    )
```

Also extend `_build_old_db` (same file) so it can still build a fixture *older* than 16 now that `SCHEMA_SQL` carries `retry_at`:

```python
    if version < 16:
        drop_lines = (*drop_lines, "retry_at         TEXT,", "-- set while status = 'rate_limited'")
```

Add this right after the existing `if version < 14:` block, inside `_build_old_db`.

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd /Users/omidkarami/Projects/Kraft/.claude/worktrees/bridge-cse_01Y5XVn26cq7yTSbv3WiynLf && uv run pytest tests/test_db.py -k "rate_limited or retry_at" -v`
Expected: FAIL — `retry_at` is not a real column yet, `SCHEMA_VERSION` is 15, and the CHECK constraints reject `'rate_limited'` for the wrong reason (both tables still lack it, `_build_old_db`'s new `drop_lines` entries are no-ops against unmodified `SCHEMA_SQL`).

- [ ] **Step 3: Bump `SCHEMA_VERSION` and widen `SCHEMA_SQL`**

In `src/kraft/db.py`, change:

```python
SCHEMA_VERSION = 15
```

to:

```python
SCHEMA_VERSION = 16
```

In the `work_items` block of `SCHEMA_SQL`, change:

```python
  status           TEXT NOT NULL CHECK (status IN
                     ('active', 'needs_human', 'completed', 'paused', 'abandoned')),
```

to:

```python
  status           TEXT NOT NULL CHECK (status IN
                     ('active', 'needs_human', 'completed', 'paused', 'abandoned', 'rate_limited')),
```

and, right after the `branch` column (before `created_at`), add:

```python
  -- set while status = 'rate_limited': when the agent's rate limit resets and
  -- `rate_limit_retry.poller` may relaunch the item. NULL otherwise.
  retry_at         TEXT,
```

In the `worker_sessions` block of `SCHEMA_SQL`, change:

```python
  status         TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown',
                    'done_with_concerns', 'needs_context')),
```

to:

```python
  status         TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown',
                    'done_with_concerns', 'needs_context', 'rate_limited')),
```

- [ ] **Step 4: Add migration 15 → 16**

In `_MIGRATIONS`, add a new key `15` (the dict's key is the *from*-version, matching every other entry) right after key `14`'s block:

```python
    # 'rate_limited' joins both CHECKs, and work_items gains `retry_at`; SQLite
    # cannot alter a constraint, so both tables are rebuilt the same 12-step way
    # migrations 4 and 9 used.
    15: [
        """CREATE TABLE work_items_new (
  id               TEXT PRIMARY KEY,
  bead_id          TEXT,
  title            TEXT NOT NULL,
  description      TEXT,
  repo             TEXT NOT NULL,
  chain_template   TEXT NOT NULL,
  chain_definition TEXT NOT NULL,
  current_node_id  TEXT,
  status           TEXT NOT NULL CHECK (status IN
                     ('active', 'needs_human', 'completed', 'paused', 'abandoned', 'rate_limited')),
  pending_steer_context TEXT,
  submodules       TEXT,
  root_merge_policy TEXT,
  attachments      TEXT,
  base_ref         TEXT,
  bead_cwd         TEXT,
  branch           TEXT,
  retry_at         TEXT,
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
)""",
        """INSERT INTO work_items_new (id, bead_id, title, description, repo, chain_template,
  chain_definition, current_node_id, status, pending_steer_context, submodules,
  root_merge_policy, attachments, base_ref, bead_cwd, branch, created_at, updated_at)
SELECT id, bead_id, title, description, repo, chain_template, chain_definition,
       current_node_id, status, pending_steer_context, submodules, root_merge_policy,
       attachments, base_ref, bead_cwd, branch, created_at, updated_at FROM work_items""",
        "DROP TABLE work_items",
        "ALTER TABLE work_items_new RENAME TO work_items",
        """CREATE TABLE worker_sessions_new (
  id             TEXT PRIMARY KEY,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  node_id        TEXT NOT NULL,
  hook_point     TEXT NOT NULL,
  pid            INTEGER,
  pid_start_time REAL,
  log_path       TEXT NOT NULL,
  result_path    TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown',
                    'done_with_concerns', 'needs_context', 'rate_limited')),
  attempt        INTEGER NOT NULL DEFAULT 1,
  session_summary_ref TEXT,
  created_at     TEXT NOT NULL,
  started_at     TEXT,
  round          INTEGER NOT NULL DEFAULT 0,
  model          TEXT,
  tokens_in      INTEGER,
  tokens_out     INTEGER,
  cost_usd       REAL,
  wall_ms        INTEGER,
  exited_at      TEXT
)""",
        """INSERT INTO worker_sessions_new (id, work_item_id, node_id, hook_point, pid,
  pid_start_time, log_path, result_path, status, attempt, session_summary_ref,
  created_at, started_at, round, model, tokens_in, tokens_out, cost_usd, wall_ms,
  exited_at)
SELECT id, work_item_id, node_id, hook_point, pid, pid_start_time, log_path,
       result_path, status, attempt, session_summary_ref, created_at, started_at,
       round, model, tokens_in, tokens_out, cost_usd, wall_ms, exited_at
FROM worker_sessions""",
        "DROP TABLE worker_sessions",
        "ALTER TABLE worker_sessions_new RENAME TO worker_sessions",
        "CREATE INDEX idx_worker_sessions_status ON worker_sessions(status)",
    ],
```

Note `retry_at` is deliberately absent from the `INSERT ... SELECT` column list — the new column has nothing to backfill, and every pre-existing row gets `NULL` by SQLite's default.

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS, all of it — this file's other migration tests must still pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/db.py tests/test_db.py
git commit -m "feat(db): add rate_limited status and retry_at column (v15->v16)"
```

---

## Task 2: Policy — `rate_limit_retries` cap

**Files:**
- Modify: `src/kraft/policy.py`
- Modify: `templates/policy.yaml`
- Test: `tests/test_policy.py`

**Interfaces:**
- Produces: `Policy.rate_limit_retries: int` (default `5`), validated by `load_policy`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_policy.py`:

```python
def test_policy_defaults_rate_limit_retries_to_five():
    p = policy.Policy(loops={}, default=policy.Cap(attempts=3, wall_clock_s=100))
    assert p.rate_limit_retries == 5


def test_load_policy_reads_rate_limit_retries(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text(
        "default: { attempts: 2, wall_clock_s: 20 }\nrate_limit_retries: 8\n"
    )
    p = policy.load_policy(d)
    assert p.rate_limit_retries == 8


def test_load_policy_defaults_rate_limit_retries_when_absent(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text("default: { attempts: 2, wall_clock_s: 20 }\n")
    p = policy.load_policy(d)
    assert p.rate_limit_retries == 5


def test_load_policy_rejects_bad_rate_limit_retries(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text(
        "default: { attempts: 2, wall_clock_s: 20 }\nrate_limit_retries: 0\n"
    )
    with pytest.raises(policy.PolicyError):
        policy.load_policy(d)


def test_load_shipped_policy_has_rate_limit_retries():
    p = policy.load_policy(_SHIPPED)
    assert isinstance(p.rate_limit_retries, int) and p.rate_limit_retries >= 1
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_policy.py -k rate_limit_retries -v`
Expected: FAIL — `AttributeError: 'Policy' object has no attribute 'rate_limit_retries'`.

- [ ] **Step 3: Add the field and parsing**

In `src/kraft/policy.py`, change the `Policy` dataclass:

```python
@dataclass(frozen=True)
class Policy:
    loops: dict[str, Cap]
    default: Cap
    loop_severities: frozenset[str] = DEFAULT_LOOP_SEVERITIES
    budget: Budget = NO_BUDGET
    #: How many times `rate_limit_retry.poller` may auto-relaunch the same node
    #: after a rejected rate limit before it falls back to `needs_human`. Not a
    #: `Cap`: a rate-limit wait can run for hours, and `Cap.wall_clock_s` would
    #: read that as an immediate breach.
    rate_limit_retries: int = 5
```

In `load_policy`, right before the final `return Policy(...)`:

```python
    raw_retries = data.get("rate_limit_retries", 5)
    if not isinstance(raw_retries, int) or isinstance(raw_retries, bool) or raw_retries < 1:
        raise PolicyError(f"{path.name}: 'rate_limit_retries' must be a positive int")
```

and add the field to the constructor call:

```python
    return Policy(
        loops=loops,
        default=_cap("default", data["default"]),
        loop_severities=severities,
        budget=budget,
        rate_limit_retries=raw_retries,
    )
```

- [ ] **Step 4: Document the default in the shipped policy**

In `templates/policy.yaml`, append:

```yaml

# How many times Kraft may auto-relaunch a work item after a rejected API rate
# limit before it gives up and stops for a human. Not a fix-loop cap: a rate
# limit wait can run for hours, this only counts attempts.
rate_limit_retries: 5
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `uv run pytest tests/test_policy.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/policy.py templates/policy.yaml tests/test_policy.py
git commit -m "feat(policy): add rate_limit_retries cap"
```

---

## Task 3: Store — `mark_rate_limited`, and clearing `retry_at`

**Files:**
- Modify: `src/kraft/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: Task 1's `work_items.retry_at` column and `'rate_limited'` status value.
- Produces: `store.mark_rate_limited(conn, work_item_id, node_id, retry_at) -> None`, appending a `work_item_rate_limited` event. `store.mark_needs_human` and `store.retry_after_cap` now also clear `retry_at` to `NULL`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_store.py`:

```python
def test_mark_rate_limited_sets_status_and_retry_at(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
            )
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, retry_at FROM work_items WHERE id='w1'"
                ).fetchone()
            )
            assert row["status"] == "rate_limited"
            assert row["retry_at"] == "2026-09-10T00:00:00Z"
            ev = database.read(lambda c: events.read_after(c, 0))[-1]
            assert ev["type"] == "work_item_rate_limited"
            assert ev["payload"] == {
                "node_id": "implementation",
                "retry_at": "2026-09-10T00:00:00Z",
            }
        finally:
            await database.close()

    asyncio.run(scenario())


def test_mark_needs_human_clears_retry_at(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
            )
            await database.write(lambda c: store.mark_needs_human(c, "w1", "implementation", "boom"))
            row = database.read(
                lambda c: c.execute("SELECT status, retry_at FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["status"] == "needs_human"
            assert row["retry_at"] is None
        finally:
            await database.close()

    asyncio.run(scenario())


def test_retry_after_cap_clears_retry_at(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.mark_rate_limited(c, "w1", "implementation", "2026-09-10T00:00:00Z")
            )
            await database.write(
                lambda c: store.retry_after_cap(c, "w1", "implementation", None, "go")
            )
            row = database.read(
                lambda c: c.execute("SELECT status, retry_at FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["status"] == "active"
            assert row["retry_at"] is None
        finally:
            await database.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_store.py -k "rate_limited or retry_at" -v`
Expected: FAIL — `AttributeError: module 'kraft.store' has no attribute 'mark_rate_limited'`.

- [ ] **Step 3: Add `mark_rate_limited`, and clear `retry_at` in the other two**

In `src/kraft/store.py`, change `mark_needs_human`'s UPDATE:

```python
    conn.execute(
        "UPDATE work_items SET status = 'needs_human', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
```

to:

```python
    conn.execute(
        "UPDATE work_items SET status = 'needs_human', retry_at = NULL, updated_at = ? "
        "WHERE id = ?",
        (_now(), work_item_id),
    )
```

Change `retry_after_cap`'s UPDATE:

```python
    conn.execute(
        "UPDATE work_items SET status = 'active', updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
```

to:

```python
    conn.execute(
        "UPDATE work_items SET status = 'active', retry_at = NULL, updated_at = ? WHERE id = ?",
        (_now(), work_item_id),
    )
```

Add a new function right after `mark_needs_human` (before `mark_completed`):

```python
def mark_rate_limited(conn: sqlite3.Connection, work_item_id: str, node_id: str, retry_at: str) -> None:
    """The item hit an API rate limit; `rate_limit_retry.poller` relaunches it
    once `retry_at` passes, with nobody paged (unlike `mark_needs_human`)."""
    conn.execute(
        "UPDATE work_items SET status = 'rate_limited', retry_at = ?, updated_at = ? WHERE id = ?",
        (retry_at, _now(), work_item_id),
    )
    events.append(
        conn, work_item_id, "work_item_rate_limited", {"node_id": node_id, "retry_at": retry_at}
    )
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run pytest tests/test_store.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/store.py tests/test_store.py
git commit -m "feat(store): add mark_rate_limited, clear retry_at on needs_human/retry"
```

---

## Task 4: Detection — parse a rejected `rate_limit_event` out of the log

**Files:**
- Modify: `src/kraft/adapters/subprocess.py`
- Modify: `tests/support/fake_agent.py`
- Test: `tests/test_adapters_subprocess.py`

**Interfaces:**
- Consumes: Task 1's `worker_sessions` CHECK accepting `'rate_limited'`.
- Produces: `subprocess._rate_limit_rejection(log_path: Path) -> dict | None` returning `{"rate_limit_type": str | None, "resets_at": int | float, "resets_at_iso": str}` or `None`. `run_task` returns `"rate_limited"` and appends a `rate_limit_hit` event when this fires, in place of `post_resolve`.

- [ ] **Step 1: Write the failing unit tests for the parser**

Add to `tests/test_adapters_subprocess.py` (check the top of that file first for its existing imports — add `from kraft.adapters.subprocess import _rate_limit_rejection` alongside whatever it already imports from that module):

```python
def test_rate_limit_rejection_reads_a_rejected_event(tmp_path):
    log = tmp_path / "s.log"
    log.write_text(
        '{"type":"assistant","message":{}}\n'
        '{"type":"rate_limit_event","rate_limit_info":{"status":"rejected",'
        '"resetsAt":1788968400,"rateLimitType":"five_hour"}}\n'
        '{"type":"result","is_error":true}\n'
    )
    got = _rate_limit_rejection(log)
    assert got == {
        "rate_limit_type": "five_hour",
        "resets_at": 1788968400,
        "resets_at_iso": "2026-09-09T05:00:00+00:00",
    }


def test_rate_limit_rejection_ignores_allowed_events(tmp_path):
    """`overageStatus` can read "rejected" while the turn itself was allowed —
    only a top-level `status: "rejected"` means the launch was refused."""
    log = tmp_path / "s.log"
    log.write_text(
        '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed",'
        '"overageStatus":"rejected","resetsAt":1788986400,"rateLimitType":"five_hour"}}\n'
        '{"type":"result","is_error":false}\n'
    )
    assert _rate_limit_rejection(log) is None


def test_rate_limit_rejection_none_without_the_event(tmp_path):
    log = tmp_path / "s.log"
    log.write_text('{"type":"result","is_error":true}\n')
    assert _rate_limit_rejection(log) is None


def test_rate_limit_rejection_best_effort_on_unreadable_log(tmp_path):
    assert _rate_limit_rejection(tmp_path / "missing.log") is None
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_adapters_subprocess.py -k rate_limit_rejection -v`
Expected: FAIL — `ImportError: cannot import name '_rate_limit_rejection'`.

- [ ] **Step 3: Add the parser and wire it into `run_task`**

In `src/kraft/adapters/subprocess.py`, add `events` to the existing import:

```python
from kraft import logs, store
```

becomes:

```python
from kraft import events, logs, store
```

Add the parser right before `_resolve` (after `read_result_fields`):

```python
def _rate_limit_rejection(log_path: Path) -> dict | None:
    """The rejected `rate_limit_info` from a stream-json log, or None.

    The CLI emits a `rate_limit_event` line on most turns, nearly all of them
    `status: "allowed"` -- an `overageStatus` of "rejected" on an otherwise
    allowed turn means only that overage spend was refused, not that the turn
    itself was blocked. Only a top-level `status: "rejected"` means the launch
    was refused. Scanned across every line, not just the last: unlike the
    result envelope, this event is not guaranteed to be the final line.
    Best-effort like `agent._envelope_is_error`: a log Kraft cannot read yet is
    "no rejection seen", not a crash.
    """
    try:
        lines = [ln for ln in log_path.read_text().splitlines() if ln.strip()]
    except OSError:
        return None
    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "rate_limit_event":
            continue
        info = obj.get("rate_limit_info")
        if not isinstance(info, dict) or info.get("status") != "rejected":
            continue
        resets_at = info.get("resetsAt")
        if not isinstance(resets_at, int | float):
            continue
        return {
            "rate_limit_type": info.get("rateLimitType"),
            "resets_at": resets_at,
            "resets_at_iso": datetime.fromtimestamp(resets_at, UTC).isoformat(),
        }
    return None
```

In `run_task`, change:

```python
    returncode = proc.returncode
    status = _resolve(result_path, returncode)
    if post_resolve is not None:
        status = post_resolve(status, log_path, returncode)
```

to:

```python
    returncode = proc.returncode
    status = _resolve(result_path, returncode)
    rate_limit = _rate_limit_rejection(log_path)
    if rate_limit is not None:
        # A rejected launch produced no artifact by construction, so this
        # skips `post_resolve` (agent.py's artifact-presence check) entirely
        # rather than let it downgrade an already-correct status to "failed".
        status = "rate_limited"
        await db.write(
            lambda c, rl=rate_limit: events.append(
                c, work_item_id, "rate_limit_hit", {**rl, "node_id": node_id}
            )
        )
    elif post_resolve is not None:
        status = post_resolve(status, log_path, returncode)
```

- [ ] **Step 4: Run the parser tests to confirm they pass**

Run: `uv run pytest tests/test_adapters_subprocess.py -v`
Expected: PASS, this whole file.

- [ ] **Step 5: Give the fake agent a `rate_limit` mode, for Task 5's end-to-end test**

In `tests/support/fake_agent.py`, change:

```python
    result_path = os.environ.get("KRAFT_RESULT_PATH")
    if mode != "error" and result_path:
```

to:

```python
    result_path = os.environ.get("KRAFT_RESULT_PATH")
    if mode not in ("error", "rate_limit") and result_path:
```

(leaves the rest of that `if` block, including its `pathlib.Path(result_path).write_text(...)` at the end, untouched — a rejected launch writes no result file, same as `error` mode today.)

Then change:

```python
    print(json.dumps({"type": "system", "subtype": "init", "model": "fake-agent"}), flush=True)
    print(
        json.dumps(
            {
                "type": "assistant",
                "request_id": "req_1",
                "message": {
                    "model": "fake-agent",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 200,
                        "cache_read_input_tokens": 500,
                    },
                },
            }
        ),
        flush=True,
    )
    envelope = {
        "type": "result",
        "is_error": mode == "error",
```

to:

```python
    print(json.dumps({"type": "system", "subtype": "init", "model": "fake-agent"}), flush=True)
    print(
        json.dumps(
            {
                "type": "assistant",
                "request_id": "req_1",
                "message": {
                    "model": "fake-agent",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 200,
                        "cache_read_input_tokens": 500,
                    },
                },
            }
        ),
        flush=True,
    )
    if mode == "rate_limit":
        print(
            json.dumps(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {
                        "status": "rejected",
                        "resetsAt": int(os.environ.get("KRAFT_FAKE_AGENT_RESETS_AT", "1788968400")),
                        "rateLimitType": "five_hour",
                    },
                }
            ),
            flush=True,
        )
    envelope = {
        "type": "result",
        "is_error": mode in ("error", "rate_limit"),
```

Also update the module docstring's mode line:

```python
CWD is the worktree. Mode via KRAFT_FAKE_AGENT: fix (default) | noop | error.
```

to:

```python
CWD is the worktree. Mode via KRAFT_FAKE_AGENT: fix (default) | noop | error |
rate_limit (rejects with KRAFT_FAKE_AGENT_RESETS_AT, default 1788968400).
```

- [ ] **Step 6: Run the whole subprocess test file once more**

Run: `uv run pytest tests/test_adapters_subprocess.py -v`
Expected: PASS — this step only touched test support, but a broken fake agent would show up here first.

- [ ] **Step 7: Commit**

```bash
git add src/kraft/adapters/subprocess.py tests/support/fake_agent.py tests/test_adapters_subprocess.py
git commit -m "feat(subprocess): detect a rejected rate_limit_event in the agent log"
```

---

## Task 5: Executor — stop the walk without a fix loop, schedule the retry

**Files:**
- Modify: `src/kraft/executor.py`
- Test: `tests/test_executor.py`

**Interfaces:**
- Consumes: `"rate_limited"` as a possible return from `_dispatch`/`_agent.run_agent_task`/`_subprocess.run_task` (Task 4); `store.mark_rate_limited` (Task 3).
- Produces: `executor.RATE_LIMITED = "rate_limited"`; `_walk_node`, `run`, and `resume` may return `"rate_limited"`.

- [ ] **Step 1: Write the failing end-to-end test**

Add to `tests/test_executor.py`, after `test_run_verify_failure_stops_at_verify`:

```python
def test_rate_limit_stops_the_chain_without_a_fix_loop(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "rate_limit")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database,
                rd,
                title="t",
                repo=str(repo),
                template=_quick_task(),
                bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker)
            )
            assert result == "rate_limited"

            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id, retry_at FROM work_items WHERE id = ?",
                    (wid,),
                ).fetchone()
            )
            assert row["status"] == "rate_limited"
            assert row["current_node_id"] == "implementation"
            assert row["retry_at"] == "2026-09-09T05:00:00+00:00"

            types = _events(database, wid)
            assert "rate_limit_hit" in types
            assert "work_item_rate_limited" in types
            # No fix loop, no repair task, no human page for this stop.
            assert "work_item_needs_human" not in types
            assert "node_recovery_started" not in types
        finally:
            await database.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_executor.py -k test_rate_limit_stops_the_chain -v`
Expected: FAIL — today the fake agent's rejected result file (none written) plus `is_error: true` resolve to plain `"failed"`, and the item lands on `needs_human` with `result == "needs_human"`.

- [ ] **Step 3: Add the `RATE_LIMITED` sentinel and propagate it**

In `src/kraft/executor.py`, right after:

```python
BUDGET = "budget"
```

add:

```python
#: `"rate_limited"` (`_subprocess.run_task`, via Task 4's `_rate_limit_rejection`).
#: Ranked above `BUDGET`/`"failed"` in `_measure_node` -- a rate limit is not
#: evidence of bad code -- but below `"paused"`, which is always a human's own
#: SIGTERM.
RATE_LIMITED = "rate_limited"
```

Right after `_stop_for_budget` (before the `REVIEW_HOOKS` constant), add:

```python
def _latest_rate_limit(db, work_item_id: str) -> dict | None:
    """The most recently appended `rate_limit_hit` event's payload, or None.

    Same reverse-scan idiom as `_last_measurement`/`_needs_context_question`:
    the event was just written by `_subprocess.run_task` in the same session
    this verdict came from, so the latest one is always the right one.
    """
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] == "rate_limit_hit":
            return e["payload"]
    return None


async def _stop_for_rate_limit(db, work_item_id: str, node: dict) -> str:
    info = _latest_rate_limit(db, work_item_id) or {}
    retry_at = info.get("resets_at_iso") or _now()
    await db.write(lambda c: store.mark_rate_limited(c, work_item_id, node["id"], retry_at))
    return RATE_LIMITED
```

In `_measure_node`, change:

```python
    if any(r == "paused" for r in results):
        return "paused", [], []
    # Logged before the BUDGET rung returns: a co-task can raise in the same node
    # as a budget-refused agent, and that traceback is the only record of it.
    excs = [r for r in results if isinstance(r, BaseException)]
    for exc in excs:
        logger.error("measuring task raised in node %s: %r", node["id"], exc)
    # paused > budget > failed. A pause is a human's instruction and outranks
    # everything. A budget breach outranks a co-task's failure because the agent
    # never ran, so its "failure" is not evidence about the code.
    if any(r == BUDGET for r in results):
        return BUDGET, [], []
```

to:

```python
    if any(r == "paused" for r in results):
        return "paused", [], []
    if any(r == RATE_LIMITED for r in results):
        return RATE_LIMITED, [], []
    # Logged before the BUDGET rung returns: a co-task can raise in the same node
    # as a budget-refused agent, and that traceback is the only record of it.
    excs = [r for r in results if isinstance(r, BaseException)]
    for exc in excs:
        logger.error("measuring task raised in node %s: %r", node["id"], exc)
    # paused > rate_limited > budget > failed. A pause is a human's instruction
    # and outranks everything. A rate limit and a budget breach both outrank a
    # co-task's failure because the agent's "failure" is not evidence about the
    # code; a rate limit outranks a budget breach because it is Kraft's own
    # spend policy refusing to start, not an external constraint the agent hit.
    if any(r == BUDGET for r in results):
        return BUDGET, [], []
```

In `_walk_node`'s plain-node branch, change:

```python
        if verdict == "paused":
            return "paused"
        if verdict == BUDGET:
            return await _stop_for_budget(db, work_item_id, node, budget)
        if verdict == "failed":
```

(the first occurrence, inside `if not key:`) to:

```python
        if verdict == "paused":
            return "paused"
        if verdict == RATE_LIMITED:
            return await _stop_for_rate_limit(db, work_item_id, node)
        if verdict == BUDGET:
            return await _stop_for_budget(db, work_item_id, node, budget)
        if verdict == "failed":
```

In `_walk_node`'s fix-loop branch (the `while True:` loop), change the second occurrence of the same three lines:

```python
        if verdict == "paused":
            return "paused"
        if verdict == BUDGET:
            return await _stop_for_budget(db, work_item_id, node, budget)

        previous_prints, fix_ran = _last_measurement(db, work_item_id, node["id"])
```

to:

```python
        if verdict == "paused":
            return "paused"
        if verdict == RATE_LIMITED:
            return await _stop_for_rate_limit(db, work_item_id, node)
        if verdict == BUDGET:
            return await _stop_for_budget(db, work_item_id, node, budget)

        previous_prints, fix_ran = _last_measurement(db, work_item_id, node["id"])
```

In `run`, change:

```python
        if result == "paused":
            return "paused"
        if result == "needs_human":
            return "needs_human"
        if await _maybe_gate(db, work_item_id, node):
            return "awaiting_gate"
```

to:

```python
        if result == "paused":
            return "paused"
        if result == "needs_human":
            return "needs_human"
        if result == RATE_LIMITED:
            return RATE_LIMITED
        if await _maybe_gate(db, work_item_id, node):
            return "awaiting_gate"
```

In `resume`'s trailing loop, change:

```python
    for node in nodes[start + 1 :]:
        if (
            await _walk_node(
                db,
                run_dirs,
                work_item_id,
                node,
                row,
                registry,
                worktree,
                policy=policy,
                launch=launch,
            )
            == "needs_human"
        ):
            return "needs_human"
        if await _maybe_gate(db, work_item_id, node):
            return "awaiting_gate"
```

to:

```python
    for node in nodes[start + 1 :]:
        tail_result = await _walk_node(
            db,
            run_dirs,
            work_item_id,
            node,
            row,
            registry,
            worktree,
            policy=policy,
            launch=launch,
        )
        if tail_result == "needs_human":
            return "needs_human"
        if tail_result == RATE_LIMITED:
            return RATE_LIMITED
        if await _maybe_gate(db, work_item_id, node):
            return "awaiting_gate"
```

- [ ] **Step 4: Run the tests to confirm they pass**

Run: `uv run pytest tests/test_executor.py -v`
Expected: PASS, this whole file — including every existing BUDGET/paused/needs_human test, unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/executor.py tests/test_executor.py
git commit -m "feat(executor): stop a rate-limited node without a fix loop, schedule retry_at"
```

---

## Task 6: Poller — relaunch a due item automatically

**Files:**
- Create: `src/kraft/rate_limit_retry.py`
- Modify: `src/kraft/api.py`
- Test: `tests/test_rate_limit_retry.py`

**Interfaces:**
- Consumes: `store.bump_counter`/`policy.Cap` (existing), `store.retry_after_cap`/`store.mark_needs_human` (Task 3), `executor.run` (existing, Task 5's `RATE_LIMITED` return is not needed here — the poller only *starts* a fresh `executor.run`).
- Produces: `rate_limit_retry.RESUME_PROMPT: str`, `rate_limit_retry.tick(app) -> list[str]`, `rate_limit_retry.poller(app) -> None` (never returns; same shape as `intake.tick`/`intake.poller`).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rate_limit_retry.py`:

```python
"""The rate-limit poller relaunches a due item automatically (Kraft-...)."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, policy, rate_limit_retry, store
from kraft.paths import RunDirs

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


@dataclass
class _Stub:
    state: SimpleNamespace


async def _stub(tmp_path, *, rate_limit_retries: int = 5) -> _Stub:
    rd = RunDirs(tmp_path / "run").ensure()
    database = await db.Database.open(rd.db)
    registry = fake_registry(sys.executable, _FAKE_AGENT)
    return _Stub(
        state=SimpleNamespace(
            db=database,
            run_dirs=rd,
            registry=registry,
            templates_dir=tmp_path / "templates",  # no repos.yaml: _launch degrades cleanly
            skills_dir=tmp_path / "skills",
            policy=policy.Policy(
                loops={},
                default=policy.Cap(attempts=3, wall_clock_s=3600),
                rate_limit_retries=rate_limit_retries,
            ),
            tasks={},
        )
    )


_CHAIN = (
    '{"template_id": "quick-task", "nodes": ['
    '{"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": null},'
    '{"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": null},'
    '{"id": "verify", "tasks": ["on.test.run"], "gate_after": null}]}'
)


async def _seed_rate_limited(app, *, retry_at: str, repo: str, wid: str = "w1") -> None:
    await app.state.db.write(
        lambda c: store.create_work_item(
            c,
            id=wid,
            bead_id=None,
            title="t",
            repo=repo,
            chain_template="quick-task",
            chain_definition=_CHAIN,
        )
    )
    await app.state.db.write(lambda c: store.enter_node(c, wid, "implementation"))
    await app.state.db.write(lambda c: store.mark_rate_limited(c, wid, "implementation", retry_at))


def _run(build, body):
    async def main():
        app = await build()
        try:
            return await body(app)
        finally:
            await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)
            await app.state.db.close()

    return asyncio.run(main())


def test_tick_ignores_a_not_yet_due_item(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)

    async def body(app):
        await _seed_rate_limited(app, retry_at="2999-01-01T00:00:00+00:00", repo=str(repo))
        assert await rate_limit_retry.tick(app) == []
        row = app.state.db.read(
            lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
        )
        assert row["status"] == "rate_limited"

    _run(lambda: _stub(tmp_path), body)


def test_tick_relaunches_a_due_item(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")  # leaves the calc bug in place, on purpose
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def body(app):
        await _seed_rate_limited(app, retry_at="2000-01-01T00:00:00+00:00", repo=str(repo))
        got = await rate_limit_retry.tick(app)
        assert got == ["w1"]
        await asyncio.gather(*app.state.tasks.values(), return_exceptions=True)

        row = app.state.db.read(
            lambda c: c.execute(
                "SELECT status, retry_at FROM work_items WHERE id='w1'"
            ).fetchone()
        )
        # relaunched past `implementation`; verify's fake `on.test.run` subprocess
        # then fails (KRAFT_FAKE_AGENT=noop leaves the calc bug), landing needs_human
        assert row["status"] == "needs_human"
        assert row["retry_at"] is None

        types = {e["type"] for e in app.state.db.read(lambda c: events.read_after(c, 0, "w1"))}
        assert "work_item_retried" in types
        ev = [
            e["payload"]
            for e in app.state.db.read(lambda c: events.read_after(c, 0, "w1"))
            if e["type"] == "work_item_retried"
        ][0]
        assert ev["steer"] == rate_limit_retry.RESUME_PROMPT

    _run(lambda: _stub(tmp_path), body)


def test_tick_falls_back_to_needs_human_once_the_cap_breaches(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    repo = make_repo(tmp_path)

    async def body(app):
        await _seed_rate_limited(app, retry_at="2000-01-01T00:00:00+00:00", repo=str(repo))
        cap = policy.Cap(attempts=1, wall_clock_s=10**9)
        await app.state.db.write(
            lambda c: store.bump_counter(c, "w1", "rate_limit:implementation", cap)
        )  # count now 1, == attempts: one more bump breaches

        got = await rate_limit_retry.tick(app)
        assert got == []
        row = app.state.db.read(
            lambda c: c.execute("SELECT status, retry_at FROM work_items WHERE id='w1'").fetchone()
        )
        assert row["status"] == "needs_human"
        assert row["retry_at"] is None
        assert app.state.tasks == {}  # nothing was relaunched

    _run(lambda: _stub(tmp_path, rate_limit_retries=1), body)
```

- [ ] **Step 2: Run to confirm failure**

Run: `uv run pytest tests/test_rate_limit_retry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.rate_limit_retry'`.

- [ ] **Step 3: Write the poller module**

Create `src/kraft/rate_limit_retry.py`:

```python
"""Auto-relaunch a work item once its rate-limit wait is over.

Always on, unlike `intake.py`'s poller: waiting out a rate limit is not
optional behaviour a human opts into, it is what the rest of this feature
promises. Ticks on a fixed interval and relaunches through the exact code
path `POST /work-items/{id}/retry` uses -- `store.retry_after_cap` plus
`executor.run(start_index=...)` -- so a rate-limited item and a manually
retried one are put back to work the same way.
"""

from __future__ import annotations

import asyncio
import json
import logging

from kraft import policy as policy_mod
from kraft import store
from kraft.store import _now as _now  # test seam for wall-clock checks

logger = logging.getLogger(__name__)

RESUME_PROMPT = (
    "You were working on this task when the agent hit an API rate limit and "
    "stopped. That limit has now reset — continue the work from where you "
    "left off."
)

#: How often the poller checks for a due item. Fixed, unlike auto-intake's
#: `interval_s`: there is no per-operator tuning knob for this, only a fixed
#: cadence cheap enough to run unconditionally (spec "Non-goals").
_INTERVAL_S = 30

#: `bump_counter`'s `cap_wall_s` column is NOT NULL, but a rate-limit wait can
#: legitimately run for hours -- ponytail: a wall-clock cap this large is
#: effectively "no wall-clock limit"; only `count` is ever checked below.
_NO_WALL_CAP = 10**9

#: `Policy.rate_limit_retries` defaults to 5; mirrored here for the one caller
#: whose `app.state.policy` is `None` outright (an unset or invalid policy.yaml).
_DEFAULT_RETRIES = 5


def _key(node_id: str) -> str:
    """Per-node, not per-item: a node that gets rate-limited, recovers, and
    later gets rate-limited again on a *different* node must not spend the
    same budget the first node already used up."""
    return f"rate_limit:{node_id}"


async def tick(app) -> list[str]:
    """One poll. Returns the work item ids relaunched, which is usually none."""
    st = app.state
    due = st.db.read(
        lambda c: c.execute(
            "SELECT id, repo, current_node_id, chain_definition FROM work_items "
            "WHERE status = 'rate_limited' AND retry_at <= ?",
            (_now(),),
        ).fetchall()
    )
    relaunched: list[str] = []
    for row in due:
        if await _retry_one(app, row):
            relaunched.append(row["id"])
    return relaunched


async def _retry_one(app, row) -> bool:
    from kraft.api import _bd_cwd, _guard, _launch, _spawn

    st = app.state
    wid = row["id"]
    node_id = row["current_node_id"]
    retries = st.policy.rate_limit_retries if st.policy is not None else _DEFAULT_RETRIES
    cap = policy_mod.Cap(attempts=retries, wall_clock_s=_NO_WALL_CAP)
    count, _started_at, cap = await st.db.write(
        lambda c: store.bump_counter(c, wid, _key(node_id), cap)
    )
    if count > cap.attempts:
        await st.db.write(
            lambda c: store.mark_needs_human(
                c, wid, node_id, f"rate_limit retries exhausted after {count - 1} attempt(s)"
            )
        )
        return False

    await st.db.write(lambda c: store.retry_after_cap(c, wid, node_id, None, RESUME_PROMPT))
    chain = json.loads(row["chain_definition"])
    start = next(i for i, n in enumerate(chain["nodes"]) if n["id"] == node_id)
    from kraft import executor  # deferred: avoids a kraft.api <-> kraft.executor import cycle

    _spawn(
        app,
        wid,
        _guard(
            st.db,
            wid,
            executor.run(
                st.db,
                st.run_dirs,
                work_item_id=wid,
                registry=st.registry,
                bd_cwd=_bd_cwd(),
                start_index=start,
                policy=st.policy,
                steer=RESUME_PROMPT,
                launch=_launch(st, row["repo"]),
            ),
        ),
    )
    return True


async def poller(app) -> None:
    """`tick` on a fixed interval until cancelled."""
    while True:
        await asyncio.sleep(_INTERVAL_S)
        try:
            await tick(app)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- a bad tick must not end the poller
            logger.exception("rate-limit retry tick failed")
```

Note: `executor` is imported inside `_retry_one`, not at module level — `kraft.executor` does not import `kraft.rate_limit_retry`, so there is no real cycle today, but `kraft.api` (which `_retry_one` already imports from, deferred, for `_spawn`/`_guard`/`_launch`/`_bd_cwd`) does import `kraft.executor`, and importing `kraft.api` at module level here would import `kraft.executor` back through it before this module finishes defining itself. Deferring both keeps `rate_limit_retry.py` import-order-independent of `api.py`, the same reason `intake.py`'s `_start` defers its own `from kraft.api import ...`.

- [ ] **Step 4: Wire the poller into `api.py`'s lifespan**

In `src/kraft/api.py`, add the import alongside the other `kraft` submodule imports (find the line importing `intake` as `intake_mod` and add a plain import next to it):

```python
from kraft import rate_limit_retry
```

In `_STOP_BOUNDARY`, change:

```python
_STOP_BOUNDARY = (
    "work_item_needs_human",
    "gate_requested",
    "gate_rejected",
    "work_item_resumed",
    "work_item_retried",
    "work_item_completed",
)
```

to:

```python
_STOP_BOUNDARY = (
    "work_item_needs_human",
    "gate_requested",
    "gate_rejected",
    "work_item_resumed",
    "work_item_retried",
    "work_item_completed",
    "work_item_rate_limited",
)
```

In `list_work_items`, add `retry_at` to the per-item dict:

```python
            "current_node_id": r["current_node_id"],
            "bead_id": r["bead_id"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "pending_gate": pending.get(r["id"]),
            "attachments": json.loads(r["attachments"]) if r["attachments"] else [],
```

to:

```python
            "current_node_id": r["current_node_id"],
            "bead_id": r["bead_id"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
            "pending_gate": pending.get(r["id"]),
            "attachments": json.loads(r["attachments"]) if r["attachments"] else [],
            "retry_at": r["retry_at"],
```

(`get_work_item`'s detail endpoint already spreads every column via `**{k: row[k] for k in row.keys()}` — `retry_at` reaches it for free, no change needed there.)

In `lifespan`, right after:

```python
    intake_task = (
        asyncio.ensure_future(intake_mod.poller(app)) if app.state.intake["enabled"] else None
    )
    app.state.intake_task = intake_task
```

add:

```python
    # Always on, unlike auto-intake: waiting out a rate limit is not optional
    # behaviour an operator enables, it is what this feature promises.
    app.state.rate_limit_task = asyncio.ensure_future(rate_limit_retry.poller(app))
```

In the `finally:` block, right after:

```python
        live_intake_task = app.state.intake_task
        if live_intake_task is not None:
            live_intake_task.cancel()
            await asyncio.gather(live_intake_task, return_exceptions=True)
```

add:

```python
        app.state.rate_limit_task.cancel()
        await asyncio.gather(app.state.rate_limit_task, return_exceptions=True)
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run: `uv run pytest tests/test_rate_limit_retry.py tests/test_api.py -v`
Expected: PASS. (`test_api.py` catches any startup/shutdown regression from the new lifespan task; check its actual filename first with `ls tests/test_api*.py` if it differs.)

- [ ] **Step 6: Commit**

```bash
git add src/kraft/rate_limit_retry.py src/kraft/api.py tests/test_rate_limit_retry.py
git commit -m "feat(api): auto-relaunch a rate-limited work item once retry_at passes"
```

---

## Task 7: Frontend — board, glyph, and timeline label

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/format.ts`
- Modify: `frontend/src/components/ui.tsx`
- Modify: `frontend/src/views/Board.tsx`
- Modify: `frontend/src/components/EventTimeline.tsx`
- Test: `frontend/src/format.test.ts`, `frontend/src/components/ui.test.tsx`, `frontend/src/views/Board.test.tsx`, `frontend/src/components/EventTimeline.test.tsx`

**Interfaces:**
- Consumes: `WorkItem.status` gains `"rate_limited"`; `WorkItem.retry_at?: string | null` (from Task 6's `list_work_items`/`get_work_item`); `SessionStatus` gains `"rate_limited"` (worker session rows, Task 1/4).

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/format.test.ts` (check its existing imports first; it likely already imports from `"./format"`):

```typescript
import { statusWord } from "./format";
// ...(alongside whatever this file already imports)

describe("statusWord", () => {
  it("renders rate_limited in plain words", () => {
    expect(statusWord("rate_limited")).toBe("rate limited");
  });

  it("falls back to the raw string for anything unmapped", () => {
    expect(statusWord("active")).toBe("active");
  });
});
```

Add to `frontend/src/components/ui.test.tsx`, inside the existing `describe("StatusGlyph / RowState", ...)` block:

```typescript
  it("has a glyph for rate_limited, not the unknown-status fallback", () => {
    render(<StatusGlyph status="rate_limited" />);
    expect(screen.getByRole("img", { name: "rate_limited" })).toBeInTheDocument();
  });
```

Add to `frontend/src/views/Board.test.tsx`:

```typescript
it("groups a rate_limited item under Running, not Needs you", () => {
  setItems(wi({ id: "w3", status: "rate_limited", current_node_id: "implementation" }));
  render(
    <MemoryRouter>
      <Board />
    </MemoryRouter>,
  );
  const running = screen.getByText("Running").closest("section")!;
  expect(within(running).getByText("Item")).toBeInTheDocument();
  const needsYou = screen.getByText("Needs you").closest("section")!;
  expect(within(needsYou).queryByText("Item")).not.toBeInTheDocument();
});

it("shows the retry time on a rate_limited card", () => {
  setItems(wi({ id: "w3", status: "rate_limited", retry_at: "2026-09-10T05:00:00Z" }));
  render(
    <MemoryRouter>
      <Board />
    </MemoryRouter>,
  );
  expect(screen.getByText(/retry/i)).toBeInTheDocument();
});
```

Add to `frontend/src/components/EventTimeline.test.tsx`:

```typescript
  it("surfaces the retry time on a rate-limit stop", () => {
    render(
      <EventTimeline
        events={[
          ev({
            type: "work_item_rate_limited",
            payload: { node_id: "implementation", retry_at: "2026-09-10T05:00:00Z" },
          }),
        ]}
      />,
    );
    expect(screen.getByText(/2026-09-10T05:00:00Z/)).toBeInTheDocument();
  });
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd frontend && npx vitest run format.test.ts src/components/ui.test.tsx src/views/Board.test.tsx src/components/EventTimeline.test.tsx`
Expected: FAIL — `"rate_limited"` is not assignable to `WorkItemStatus`/`SessionStatus` (TS compile error inside vitest), `GLYPHS` has no entry for it, `statusWord` falls back to the raw string, `Board` has no "Running"-bucket membership for it, and `detailOf` returns `null` for `work_item_rate_limited`.

- [ ] **Step 3: Widen the types**

In `frontend/src/types.ts`, change:

```typescript
export type WorkItemStatus =
  | "active"
  | "needs_human"
  | "completed"
  | "paused"
  // Terminal, and off the board unless explicitly asked for (Kraft-x85).
  | "abandoned";
```

to:

```typescript
export type WorkItemStatus =
  | "active"
  | "needs_human"
  | "completed"
  | "paused"
  // Terminal, and off the board unless explicitly asked for (Kraft-x85).
  | "abandoned"
  // Waiting on an API rate limit to reset; the poller relaunches it, no
  // human paged.
  | "rate_limited";
```

Add a field to `WorkItem`, right after `pending_steer_context`:

```typescript
  /** Steer text left while paused; consumed by the next agent launch. */
  pending_steer_context?: string | null;
```

becomes:

```typescript
  /** Steer text left while paused; consumed by the next agent launch. */
  pending_steer_context?: string | null;
  /** Set while `status === "rate_limited"`: when the poller may relaunch it. */
  retry_at?: string | null;
```

Change:

```typescript
export type SessionStatus =
  | "pending"
  | "running"
  | "done"
  | "done_with_concerns"
  | "needs_context"
  | "failed"
  | "capped_out"
  | "paused"
  | "unknown";
```

to:

```typescript
export type SessionStatus =
  | "pending"
  | "running"
  | "done"
  | "done_with_concerns"
  | "needs_context"
  | "failed"
  | "capped_out"
  | "paused"
  | "unknown"
  | "rate_limited";
```

- [ ] **Step 4: Add the status word and the glyph**

In `frontend/src/format.ts`, change:

```typescript
const STATUS_WORDS: Record<string, string> = {
  capped_out: "capped out",
  needs_human: "needs you",
};
```

to:

```typescript
const STATUS_WORDS: Record<string, string> = {
  capped_out: "capped out",
  needs_human: "needs you",
  rate_limited: "rate limited",
};
```

In `frontend/src/components/ui.tsx`, add `Clock` to the Phosphor import:

```typescript
import {
  Check,
  ChatText,
  Circle,
  CircleNotch,
  DotsThree,
  Pause,
  Prohibit,
  Question,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";
```

to:

```typescript
import {
  Check,
  ChatText,
  Circle,
  Clock,
  CircleNotch,
  DotsThree,
  Pause,
  Prohibit,
  Question,
  WarningCircle,
  XCircle,
} from "@phosphor-icons/react";
```

Change `GLYPHS`:

```typescript
const GLYPHS: Record<SessionStatus, typeof Check> = {
  running: CircleNotch,
  done: Check,
  done_with_concerns: WarningCircle,
  needs_context: ChatText,
  failed: XCircle,
  capped_out: Prohibit,
  paused: Pause,
  unknown: Question,
  // Design gap: `pending` is not in the spec's status table. It takes the
  // quietest ring in the vocabulary rather than inventing a glyph.
  pending: Circle,
};
```

to:

```typescript
const GLYPHS: Record<SessionStatus, typeof Check> = {
  running: CircleNotch,
  done: Check,
  done_with_concerns: WarningCircle,
  needs_context: ChatText,
  failed: XCircle,
  capped_out: Prohibit,
  paused: Pause,
  unknown: Question,
  // Design gap: `pending` is not in the spec's status table. It takes the
  // quietest ring in the vocabulary rather than inventing a glyph.
  pending: Circle,
  rate_limited: Clock,
};
```

- [ ] **Step 5: Put a rate_limited item under "Running" and show its retry time**

In `frontend/src/views/Board.tsx`, add `Clock` to the Phosphor import and `clock` to the format import:

```typescript
import { Prohibit } from "@phosphor-icons/react";
```

to:

```typescript
import { Clock, Prohibit } from "@phosphor-icons/react";
```

```typescript
import { ago, repoName } from "../format";
```

to:

```typescript
import { ago, clock, repoName } from "../format";
```

Change the `STATUS_GROUPS` "Running" entry:

```typescript
  { id: "running", label: "Running", test: (i) => i.status === "active" },
```

to:

```typescript
  {
    id: "running",
    label: "Running",
    // A rate-limited item is not mid-agent-call, but it is not waiting on a
    // person either -- the poller drives it forward on its own, the same
    // story "Running" already tells for an active item.
    test: (i) => i.status === "active" || i.status === "rate_limited",
  },
```

In `BoardRow`, change:

```typescript
function BoardRow({ item }: { item: WorkItem }) {
  const gate = item.status === "needs_human" ? (item.pending_gate ?? null) : null;
  const capped = item.status === "completed" ? null : item.cappedOut;
```

to:

```typescript
function BoardRow({ item }: { item: WorkItem }) {
  const gate = item.status === "needs_human" ? (item.pending_gate ?? null) : null;
  const capped = item.status === "completed" ? null : item.cappedOut;
  const retryAt = item.status === "rate_limited" ? item.retry_at : null;
```

and change the current-node tags block:

```typescript
      <div className="board-row-current">
        {item.current_node_id}
        {item.fixCycle != null && (
          <span className="tag tag-outline tag-tight">fix·{item.fixCycle}</span>
        )}
        {capped && (
          <span className="tag tag-outline tag-tight">
            <Prohibit size={10} />
            capped {capped.cycles}/{capped.attempts}
          </span>
        )}
      </div>
```

to:

```typescript
      <div className="board-row-current">
        {item.current_node_id}
        {item.fixCycle != null && (
          <span className="tag tag-outline tag-tight">fix·{item.fixCycle}</span>
        )}
        {capped && (
          <span className="tag tag-outline tag-tight">
            <Prohibit size={10} />
            capped {capped.cycles}/{capped.attempts}
          </span>
        )}
        {retryAt && (
          <span className="tag tag-outline tag-tight">
            <Clock size={10} />
            retry {clock(retryAt)}
          </span>
        )}
      </div>
```

- [ ] **Step 6: Surface the retry time in the timeline**

In `frontend/src/components/EventTimeline.tsx`, change:

```typescript
  if (e.type === "work_item_needs_human" && typeof p.reason === "string") return p.reason;
```

to:

```typescript
  if (e.type === "work_item_needs_human" && typeof p.reason === "string") return p.reason;
  if (e.type === "work_item_rate_limited" && typeof p.retry_at === "string") {
    return `retries at ${p.retry_at}`;
  }
```

- [ ] **Step 7: Run the tests to confirm they pass**

Run: `cd frontend && npx vitest run format.test.ts src/components/ui.test.tsx src/views/Board.test.tsx src/components/EventTimeline.test.tsx`
Expected: PASS.

Then run the full frontend suite and typecheck, since a `Record<SessionStatus, ...>` / `Record<WorkItemStatus, ...>` elsewhere would only show up as a compile error, not a targeted test failure:

Run: `just test-ui`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/types.ts frontend/src/format.ts frontend/src/components/ui.tsx \
        frontend/src/views/Board.tsx frontend/src/components/EventTimeline.tsx \
        frontend/src/format.test.ts frontend/src/components/ui.test.tsx \
        frontend/src/views/Board.test.tsx frontend/src/components/EventTimeline.test.tsx
git commit -m "feat(ui): show rate_limited work items on the board and timeline"
```

---

## Final check

- [ ] Run the full backend suite: `uv run pytest -q` — expected PASS (this is the one point in the plan where the full, not targeted, suite is worth the ~14 min: seven tasks touched shared functions — `store.mark_needs_human`, `_measure_node`, `_walk_node`, `run`, `resume` — each used by many other tests).
- [ ] Run `just lint` — expected clean.
- [ ] Run `just test-ui` — expected PASS.
