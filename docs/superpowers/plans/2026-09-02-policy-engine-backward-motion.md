# Policy Engine + Backward-Motion Fix Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `kraft.policy` module, a `retry_counters` table with the project's first schema migration, and a bounded fix loop for the `verify` node — so a failing `on.test.run` triggers up to N fix-scoped `on.implementation.start` cycles before escalating to `needs_human`.

**Architecture:** `kraft.policy` is pure cap math (`load_policy` reads `templates/policy.yaml`; `resolve_cap`; `check`). The "backward-motion coordinator" is a loop inside `executor._walk_node`, entered only for a node carrying a `fix_loop` key. On non-clean measuring results it bumps a `retry_counters` row, checks the cap (attempts **and** wall-clock), and either runs one fix task and re-measures or marks the node's sessions `capped_out` and the work item `needs_human`. Separately, `executor.resume` learns to skip a gate at the current node that the event log shows was already approved (Kraft-2v0).

**Tech Stack:** Python 3.12+, asyncio, SQLite (WAL, single serialized writer), FastAPI, pytest, YAML templates. `uv run` for everything.

**Spec:** `docs/superpowers/specs/2026-09-02-policy-engine-backward-motion-design.md`

## Global Constraints

- Python 3.12+, `asyncio` throughout. All DB writes go through `db.write(fn)` (single serialized writer); reads through `db.read(fn)`.
- Timestamps are ISO-8601 UTC strings from `store._now()`. Never store or compare naive datetimes.
- `events.type` is free-text (no CHECK) — new event types need no migration. `work_items.status` stays within the existing CHECK set `('active', 'needs_human', 'completed')`. `worker_sessions.status` CHECK already includes `capped_out`.
- The loop key string, used verbatim everywhere: `verify_fix_loop`.
- New event type, used verbatim: `fix_cycle_started`. Payload: `{"node_id": <str>, "cycle": <int>, "failed_tasks": [<hook point str>]}`.
- `policy.check` breaches when `count > cap.attempts` OR `(now - started_at) >= cap.wall_clock_s`. It is called *after* `bump_counter`, so `count` is the number of fix cycles that have run. `attempts: 3` allows 3 fix cycles; the 4th bump (count 4) breaches.
- Cap numbers in `templates/policy.yaml` are placeholders (`02_orchestrator_core.md` §13). Do not tune them; do not gate tests on the shipped values — test fixtures set their own.
- Match existing test style: `asyncio.run(scenario())` wrapping an inner async fn; helpers from `tests/support/harness.py` (`make_repo`, `isolated_bd`, `fake_registry`); a local `_types()` / `_events()` helper for event-type assertions; `monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)` or `monkeypatch.setenv(...)` to steer the fake agent.
- The fake agent (`tests/support/fake_agent.py`) has two modes: `KRAFT_FAKE_AGENT=fix` (default — rewrites `calc.py` `a - b` → `a + b`, making the sample repo's test pass) and `KRAFT_FAKE_AGENT=noop` (does nothing). Do not add modes.
- `uv run ruff check . && uv run ruff format --check .` must be clean before each commit.
- Commit after each task with a `feat:` / `test:` / `refactor:` prefix. End commit messages with `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/kraft/policy.py` | `Cap`, `Policy` dataclasses; `load_policy`, `resolve_cap`, `check`. Pure, no DB. | create |
| `templates/policy.yaml` | Loop cap defaults (`verify_fix_loop`, `default`). | create |
| `src/kraft/db.py` | `SCHEMA_VERSION = 2`; `retry_counters` in `SCHEMA_SQL`; `_MIGRATIONS` + forward-only runner in `migrate()`. | modify |
| `src/kraft/store.py` | `bump_counter`, `read_counter`, `mark_sessions_capped_out`. | modify |
| `src/kraft/templates.py` | Validate a node's `fix_loop` key; `materialize` passes it through. | modify |
| `src/kraft/executor.py` | `_measure_node` extraction; `_walk_node` fix-loop branch; `_FIX_PROMPT`; `instruction_override` on `_dispatch`; `policy` param on `run`/`resume`/`_walk_node`; `_gate_cleared` + gate-skip in `resume`. | modify |
| `src/kraft/api.py` | Load `policy.yaml` into `app.state`; pass `policy=` into `executor.run`/`resume` spawns; `invalid_policy` in `/health`. | modify |
| `templates/default.yaml` | `verify` node gains `fix_loop: verify_fix_loop`. | modify |
| `tests/test_policy.py` | Unit tests for `policy.py`. | create |
| `tests/test_fix_loop.py` | In-process fix-loop scenarios. | create |
| `tests/test_db.py` | v1→v2 migration; table count 3→4. | modify |
| `tests/test_templates.py` | `fix_loop` validity + `default.yaml` assertion. | modify |
| `tests/test_resume.py` | Approve-then-crash keeps the approval. | modify |
| `tests/support/harness.py` | `fake_templates_dir` also writes a `policy.yaml`; a `fix_loop` mini-template helper. | modify |

---

## Task 1: `kraft.policy` module + `policy.yaml`

**Files:**
- Create: `src/kraft/policy.py`
- Create: `templates/policy.yaml`
- Test: `tests/test_policy.py`

**Interfaces:**
- Consumes: nothing (pure module; `yaml` from the existing dependency set).
- Produces:
  - `policy.Cap` — `@dataclass(frozen=True)` with `attempts: int`, `wall_clock_s: int`.
  - `policy.Policy` — `@dataclass(frozen=True)` with `loops: dict[str, Cap]`, `default: Cap`.
  - `policy.PolicyError(Exception)`.
  - `policy.load_policy(path: str | Path) -> Policy` — raises `PolicyError` on a malformed doc.
  - `policy.resolve_cap(policy: Policy, key: str) -> Cap` — `policy.loops.get(key, policy.default)`.
  - `policy.check(*, count: int, started_at: str, cap: Cap, now: str) -> str` — returns `"ok"` or `"breached"`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_policy.py`:

```python
from pathlib import Path

import pytest

from kraft import policy

_SHIPPED = Path(__file__).parent.parent / "templates" / "policy.yaml"


def test_load_shipped_policy():
    p = policy.load_policy(_SHIPPED)
    assert "verify_fix_loop" in p.loops
    c = p.loops["verify_fix_loop"]
    assert isinstance(c.attempts, int) and c.attempts >= 1
    assert isinstance(c.wall_clock_s, int) and c.wall_clock_s >= 1
    assert isinstance(p.default, policy.Cap)


def test_resolve_cap_falls_back_to_default(tmp_path):
    d = tmp_path / "policy.yaml"
    d.write_text(
        "loops:\n  verify_fix_loop: { attempts: 5, wall_clock_s: 10 }\n"
        "default: { attempts: 2, wall_clock_s: 20 }\n"
    )
    p = policy.load_policy(d)
    assert policy.resolve_cap(p, "verify_fix_loop") == policy.Cap(5, 10)
    assert policy.resolve_cap(p, "nonexistent_loop") == policy.Cap(2, 20)


@pytest.mark.parametrize(
    "doc",
    [
        "loops: {}\n",  # no default
        "default: { attempts: 0, wall_clock_s: 5 }\n",  # attempts < 1
        "default: { attempts: 3 }\n",  # missing wall_clock_s
        "default: not-a-mapping\n",
        "just a string\n",
    ],
)
def test_load_policy_rejects_malformed(tmp_path, doc):
    d = tmp_path / "policy.yaml"
    d.write_text(doc)
    with pytest.raises(policy.PolicyError):
        policy.load_policy(d)


def test_check_attempts_breach():
    cap = policy.Cap(attempts=3, wall_clock_s=99999)
    now = "2026-09-02T00:00:00+00:00"
    started = "2026-09-02T00:00:00+00:00"
    assert policy.check(count=3, started_at=started, cap=cap, now=now) == "ok"
    assert policy.check(count=4, started_at=started, cap=cap, now=now) == "breached"


def test_check_wall_clock_breach():
    cap = policy.Cap(attempts=99, wall_clock_s=60)
    started = "2026-09-02T00:00:00+00:00"
    within = "2026-09-02T00:00:59+00:00"
    past = "2026-09-02T00:01:00+00:00"
    assert policy.check(count=1, started_at=started, cap=cap, now=within) == "ok"
    assert policy.check(count=1, started_at=started, cap=cap, now=past) == "breached"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_policy.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.policy'`

- [ ] **Step 3: Create `templates/policy.yaml`**

```yaml
# Cap defaults for loop-bounded work. Every number here is a placeholder
# (docs/consolidated/02_orchestrator_core.md §13) — tune against real runs.
loops:
  verify_fix_loop:   { attempts: 3, wall_clock_s: 3600 }
default:             { attempts: 3, wall_clock_s: 3600 }
```

- [ ] **Step 4: Create `src/kraft/policy.py`**

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml


class PolicyError(Exception):
    pass


@dataclass(frozen=True)
class Cap:
    attempts: int
    wall_clock_s: int


@dataclass(frozen=True)
class Policy:
    loops: dict[str, Cap]
    default: Cap


def _cap(name: str, raw: object) -> Cap:
    if not isinstance(raw, dict):
        raise PolicyError(f"{name}: expected a mapping with 'attempts' and 'wall_clock_s'")
    try:
        attempts = raw["attempts"]
        wall_clock_s = raw["wall_clock_s"]
    except (KeyError, TypeError) as exc:
        raise PolicyError(f"{name}: missing 'attempts' or 'wall_clock_s'") from exc
    if not isinstance(attempts, int) or isinstance(attempts, bool) or attempts < 1:
        raise PolicyError(f"{name}: 'attempts' must be a positive int")
    if not isinstance(wall_clock_s, int) or isinstance(wall_clock_s, bool) or wall_clock_s < 1:
        raise PolicyError(f"{name}: 'wall_clock_s' must be a positive int")
    return Cap(attempts=attempts, wall_clock_s=wall_clock_s)


def load_policy(path: str | Path) -> Policy:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"{path.name}: cannot read/parse: {exc}") from exc
    if not isinstance(data, dict) or "default" not in data:
        raise PolicyError(f"{path.name}: expected a mapping with a 'default' cap")
    loops_raw = data.get("loops") or {}
    if not isinstance(loops_raw, dict):
        raise PolicyError(f"{path.name}: 'loops' must be a mapping")
    loops = {k: _cap(f"loops.{k}", v) for k, v in loops_raw.items()}
    return Policy(loops=loops, default=_cap("default", data["default"]))


def resolve_cap(policy: Policy, key: str) -> Cap:
    return policy.loops.get(key, policy.default)


def check(*, count: int, started_at: str, cap: Cap, now: str) -> str:
    if count > cap.attempts:
        return "breached"
    elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(started_at)).total_seconds()
    if elapsed >= cap.wall_clock_s:
        return "breached"
    return "ok"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_policy.py -v`
Expected: PASS (all)

- [ ] **Step 6: Lint**

Run: `uv run ruff check src/kraft/policy.py tests/test_policy.py && uv run ruff format --check src/kraft/policy.py tests/test_policy.py`
Expected: clean

- [ ] **Step 7: Commit**

```bash
git add src/kraft/policy.py templates/policy.yaml tests/test_policy.py
git commit -m "feat: kraft.policy — cap resolution + breach check

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 2: `retry_counters` table + first schema migration + store helpers

**Files:**
- Modify: `src/kraft/db.py`
- Modify: `src/kraft/store.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `policy.Cap` (Task 1); `store._now` (exists).
- Produces:
  - `db.SCHEMA_VERSION == 2`; `retry_counters` table exists after `migrate()`.
  - `store.bump_counter(conn, work_item_id: str, key: str, cap: "policy.Cap") -> tuple[int, str]` — insert `(count=1, cap snapshot, started_at=now)` or `count = count + 1`; returns `(new_count, started_at)`.
  - `store.read_counter(conn, work_item_id: str, key: str) -> sqlite3.Row | None`.
  - `store.mark_sessions_capped_out(conn, work_item_id: str, node_id: str) -> None` — non-terminal `worker_sessions` rows for that `(work_item_id, node_id)` → `status='capped_out'`, `exited_at=now`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_db.py`, add:

```python
def test_migrate_creates_retry_counters(tmp_path):
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    assert "retry_counters" in _tables(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 2


def test_migrate_v1_to_v2_adds_retry_counters(tmp_path):
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    # Build a v1 DB by hand: run every CREATE from SCHEMA_SQL except retry_counters.
    conn.execute("BEGIN")
    for stmt in (s.strip() for s in db.SCHEMA_SQL.split(";")):
        if stmt and "retry_counters" not in stmt:
            conn.execute(stmt)
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == 2
    assert "retry_counters" in _tables(conn2)
    assert conn2.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1  # data preserved


def test_bump_counter_inserts_then_increments(tmp_path):
    import asyncio as _a

    from kraft import policy, store

    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w','t','/r','quick-task','{}','active','now','now')"
                )
            )
            cap = policy.Cap(attempts=3, wall_clock_s=100)
            n1, s1 = await database.write(
                lambda c: store.bump_counter(c, "w", "verify_fix_loop", cap)
            )
            assert n1 == 1
            n2, s2 = await database.write(
                lambda c: store.bump_counter(c, "w", "verify_fix_loop", cap)
            )
            assert n2 == 2
            assert s2 == s1  # started_at frozen at first fire
            row = database.read(lambda c: store.read_counter(c, "w", "verify_fix_loop"))
            assert row["count"] == 2
            assert row["cap_attempts"] == 3 and row["cap_wall_s"] == 100
        finally:
            await database.close()

    _a.run(scenario())
```

Update the two existing table-count assertions:
- `test_migrate_is_idempotent`: `assert table_count == 3` → `== 4`.
- `test_migrate_atomicity_rolls_back_on_error`: the two `assert tables == 3` after successful recovery → `== 4`. (The `assert tables == 0` after the failed migration stays.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db.py -v`
Expected: FAIL — `retry_counters` not in tables; `SCHEMA_VERSION` is 1; `bump_counter` missing; the idempotent/atomicity count assertions still expect 3.

- [ ] **Step 3: Add `retry_counters` to `SCHEMA_SQL` and bump the version**

In `src/kraft/db.py`, set `SCHEMA_VERSION = 2` and append to `SCHEMA_SQL` (before the closing `"""`):

```sql

CREATE TABLE retry_counters (
  work_item_id  TEXT NOT NULL REFERENCES work_items(id),
  key           TEXT NOT NULL,
  count         INTEGER NOT NULL DEFAULT 0,
  cap_attempts  INTEGER NOT NULL,
  cap_wall_s    INTEGER NOT NULL,
  started_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (work_item_id, key)
);
```

- [ ] **Step 4: Add the migration runner to `migrate()`**

In `src/kraft/db.py`, add a module constant after `SCHEMA_SQL`:

```python
_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE retry_counters (
  work_item_id  TEXT NOT NULL REFERENCES work_items(id),
  key           TEXT NOT NULL,
  count         INTEGER NOT NULL DEFAULT 0,
  cap_attempts  INTEGER NOT NULL,
  cap_wall_s    INTEGER NOT NULL,
  started_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (work_item_id, key)
)"""
    ],
}
```

Then replace the `if version != 0:` raise block in `migrate()`:

```python
    if version != 0:
        # Forward-only migration: apply each version step's statements, bump
        # user_version after each, all-or-nothing.
        try:
            conn.execute("BEGIN")
            for v in range(version, SCHEMA_VERSION):
                for stmt in _MIGRATIONS[v]:
                    conn.execute(stmt)
                conn.execute(f"PRAGMA user_version = {v + 1}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return
```

(Leave the `version == SCHEMA_VERSION` no-op and the `version > SCHEMA_VERSION` "newer than code" raise above it untouched. Leave the `version == 0` full-`SCHEMA_SQL` path untouched.)

- [ ] **Step 5: Add the store helpers**

In `src/kraft/store.py`, add (after `mark_completed`):

```python
def bump_counter(conn: sqlite3.Connection, work_item_id, key, cap) -> tuple[int, str]:
    now = _now()
    row = conn.execute(
        "SELECT count, started_at FROM retry_counters WHERE work_item_id = ? AND key = ?",
        (work_item_id, key),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO retry_counters (work_item_id, key, count, cap_attempts, "
            "cap_wall_s, started_at, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
            (work_item_id, key, cap.attempts, cap.wall_clock_s, now, now),
        )
        return 1, now
    new_count = row["count"] + 1
    conn.execute(
        "UPDATE retry_counters SET count = ?, updated_at = ? WHERE work_item_id = ? AND key = ?",
        (new_count, now, work_item_id, key),
    )
    return new_count, row["started_at"]


def read_counter(conn: sqlite3.Connection, work_item_id, key):
    return conn.execute(
        "SELECT * FROM retry_counters WHERE work_item_id = ? AND key = ?",
        (work_item_id, key),
    ).fetchone()


def mark_sessions_capped_out(conn: sqlite3.Connection, work_item_id, node_id) -> None:
    # On breach every measuring task in the node becomes capped_out (02 §7.2) —
    # including one that ended 'failed' on the final cycle. A 'done' co-task
    # (a clean noop review) is left as-is.
    conn.execute(
        "UPDATE worker_sessions SET status = 'capped_out', exited_at = ? "
        "WHERE work_item_id = ? AND node_id = ? "
        "AND status NOT IN ('done', 'capped_out')",
        (_now(), work_item_id, node_id),
    )
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_db.py -v`
Expected: PASS (all — new tests + the updated count assertions)

- [ ] **Step 7: Full regression + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: same pass count as `main` plus the 3 new `test_db` tests; 1 skip unchanged; lint clean. (Existing DBs under `.kraft-run/` in other worktrees are not touched by tests — tests use `tmp_path`.)

- [ ] **Step 8: Commit**

```bash
git add src/kraft/db.py src/kraft/store.py tests/test_db.py
git commit -m "feat: retry_counters table + forward-only schema migration (v1->v2)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 3: `fix_loop` node key — validation, materialize, `default.yaml`

**Files:**
- Modify: `src/kraft/templates.py`
- Modify: `templates/default.yaml`
- Test: `tests/test_templates.py`

**Interfaces:**
- Consumes: existing `templates.load_templates`, `templates.materialize`.
- Produces:
  - `load_templates` marks a template **invalid** if any node has `fix_loop` that is not (absent | `None` | a non-empty `str`), or has `fix_loop` set with an empty `tasks` list.
  - `materialize(template)` node dicts gain a `"fix_loop"` key (value from the node, else `None`).

- [ ] **Step 1: Write the failing tests**

In `tests/test_templates.py`, add:

```python
def test_default_yaml_verify_node_has_fix_loop():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    ts = templates.load_templates(TEMPLATES_DIR, reg)
    assert "default" in ts.valid, ts.invalid
    verify = next(n for n in ts.valid["default"].nodes if n["id"] == "verify")
    assert verify["fix_loop"] == "verify_fix_loop"


def test_materialize_carries_fix_loop():
    reg = templates.load_registry(TEMPLATES_DIR / "registry.yaml")
    tmpl = templates.load_templates(TEMPLATES_DIR, reg).valid["default"]
    mat = templates.materialize(tmpl)
    by_id = {n["id"]: n for n in mat["nodes"]}
    assert by_id["verify"]["fix_loop"] == "verify_fix_loop"
    assert by_id["env_setup"]["fix_loop"] is None


def test_non_string_fix_loop_quarantines_template(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "quick-task.yaml": GOOD_TEMPLATE,
            "badloop.yaml": (
                "id: badloop\n"
                "nodes:\n"
                "  - { id: n1, tasks: [on.test.run], fix_loop: 123 }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "quick-task" in ts.valid
    assert "badloop" in ts.invalid
    assert "fix_loop" in ts.invalid["badloop"]


def test_fix_loop_with_no_tasks_quarantines_template(tmp_path):
    d = _dir(
        tmp_path,
        **{
            "registry.yaml": REGISTRY_YAML,
            "emptyloop.yaml": (
                "id: emptyloop\n"
                "nodes:\n"
                "  - { id: n1, tasks: [], fix_loop: n1_fix_loop }\n"
            ),
        },
    )
    reg = templates.load_registry(d / "registry.yaml")
    ts = templates.load_templates(d, reg)
    assert "emptyloop" in ts.invalid
    assert "fix_loop" in ts.invalid["emptyloop"]
```

Check `tests/test_templates.py` for an existing `_dir(...)` / `GOOD_TEMPLATE` / `REGISTRY_YAML` helper (used by the `gate_after` quarantine tests from effort #1). Reuse them. If `_dir` does not exist, mirror the helper the `gate_after` tests use.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_templates.py -k "fix_loop or materialize_carries" -v`
Expected: FAIL — `default.yaml` `verify` node has no `fix_loop`; `materialize` output has no `fix_loop` key; the bad-loop templates are accepted as valid.

- [ ] **Step 3: Add `fix_loop` to `templates/default.yaml`**

Change the `verify` node only:

```yaml
  - id: verify
    tasks: [on.test.run, on.review.local.run]
    fix_loop: verify_fix_loop
```

- [ ] **Step 4: Validate `fix_loop` in `load_templates`**

In `src/kraft/templates.py`, in `load_templates`, immediately after the `bad_gates` block (which ends `continue`), add:

```python
        bad_loop = next(
            (
                n["id"]
                for n in nodes
                if "fix_loop" in n
                and n["fix_loop"] is not None
                and not (isinstance(n["fix_loop"], str) and n["fix_loop"])
            ),
            None,
        )
        if bad_loop is not None:
            invalid[tid] = (
                f"template {tid!r}: node {bad_loop!r} 'fix_loop' must be a non-empty string or null"
            )
            continue

        empty_loop = next(
            (n["id"] for n in nodes if n.get("fix_loop") and not n["tasks"]), None
        )
        if empty_loop is not None:
            invalid[tid] = (
                f"template {tid!r}: node {empty_loop!r} has 'fix_loop' but no tasks to measure"
            )
            continue
```

- [ ] **Step 5: Carry `fix_loop` through `materialize`**

In `src/kraft/templates.py`, in `materialize`, add the key to the per-node dict:

```python
            {
                "id": n["id"],
                "tasks": list(n["tasks"]),
                "gate_after": n.get("gate_after"),
                "fix_loop": n.get("fix_loop"),
            }
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_templates.py -v`
Expected: PASS (all — new tests + existing)

- [ ] **Step 7: Lint + commit**

```bash
uv run ruff check . && uv run ruff format --check .
git add src/kraft/templates.py templates/default.yaml tests/test_templates.py
git commit -m "feat: fix_loop node key — validation + materialize pass-through

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 4: The fix loop in `executor._walk_node`

**Files:**
- Modify: `src/kraft/executor.py`
- Modify: `tests/support/harness.py`
- Test: `tests/test_fix_loop.py`

**Interfaces:**
- Consumes: `policy.Policy`, `policy.resolve_cap`, `policy.check` (Task 1); `store.bump_counter`, `store.mark_sessions_capped_out` (Task 2); `store._now`, `store.enter_node`, `store.complete_node`, `store.mark_needs_human`, `events.append` (exist); `materialize` output with `fix_loop` (Task 3).
- Produces:
  - `executor._measure_node(db, run_dirs, work_item_id, node, row, registry, worktree) -> str` — enters the node, `gather`s its tasks, returns `"ok"` (all clean) or `"needs_human"` (already marks needs_human on failure, as `_walk_node` does today). **Also** exposes the failed hook points: return `tuple[str, list[str]]` — `(verdict, failed_hooks)` where `failed_hooks` is `[]` on `"ok"`.
  - `executor._walk_node(...)` signature gains keyword-only `policy=None`; behavior unchanged for a node without `fix_loop`; for a node with `fix_loop`, runs the bounded loop.
  - `executor._dispatch(...)` signature gains keyword-only `instruction_override: str | None = None`; only the `agent` branch uses it.
  - `executor._FIX_PROMPT: str` — format string with `{node_id}` and `{log_path}`.
  - `executor.run(...)` / `executor.resume(...)` signatures gain keyword-only `policy: "policy.Policy | None" = None`, threaded to `_walk_node`.

- [ ] **Step 1: Write the failing tests**

The existing `harness.fake_registry(python_exe, fake_agent_path)` already returns a
`Registry` with `on.implementation.start` → the fake agent and everything else from
the shipped `registry.yaml` (so `on.test.run` stays `{kind: subprocess, command:
[pytest, -q]}`, which runs the sample repo's own failing test). Reuse it — no
harness change needed for this task.

Create `tests/test_fix_loop.py`:

```python
import asyncio
import sys
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, policy, store
from kraft.paths import RunDirs
from kraft.templates import Template

_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _registry():
    return fake_registry(sys.executable, _FAKE_AGENT)


def _fixloop_template() -> Template:
    # env_setup builds the worktree the fix agent + pytest run inside. There is NO
    # implementation node: verify's cycle 0 sees the sample repo's still-failing
    # test, so the fix loop is what does the fixing (via on.implementation.start).
    # Minimal shape that actually exercises the loop.
    return Template(
        id="fixloop",
        nodes=[
            {"id": "env_setup", "tasks": ["on.env.prepare"],
             "gate_after": None, "fix_loop": None},
            {"id": "verify", "tasks": ["on.test.run"],
             "gate_after": None, "fix_loop": "verify_fix_loop"},
        ],
    )


def _types(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def _make_policy(tmp_path, *, attempts=3, wall_clock_s=3600) -> policy.Policy:
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"loops:\n  verify_fix_loop: {{ attempts: {attempts}, wall_clock_s: {wall_clock_s} }}\n"
        f"default: {{ attempts: {attempts}, wall_clock_s: {wall_clock_s} }}\n"
    )
    return policy.load_policy(p)


def test_fix_loop_succeeds_first_cycle(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _registry()
            pol = _make_policy(tmp_path)
            wid = await executor.intake(
                database, rd, title="make the failing test pass",
                repo=str(repo), template=_fixloop_template(), bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry,
                bd_cwd=str(tracker), policy=pol,
            )
            assert result == "completed"
            types = _types(database, wid)
            # cycle 0 fails -> 1 fix task fixes calc.py -> re-measure passes
            assert types.count("fix_cycle_started") == 1
            row = database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop"))
            assert row["count"] == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_fix_loop_cap_breach(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _registry()
            pol = _make_policy(tmp_path, attempts=2)
            wid = await executor.intake(
                database, rd, title="never fixed",
                repo=str(repo), template=_fixloop_template(), bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry,
                bd_cwd=str(tracker), policy=pol,
            )
            assert result == "needs_human"
            types = _types(database, wid)
            assert types.count("fix_cycle_started") == 2
            wi = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            assert wi["status"] == "needs_human"
            assert wi["current_node_id"] == "verify"
            caps = database.read(
                lambda c: c.execute(
                    "SELECT status FROM worker_sessions WHERE work_item_id=? AND node_id='verify'",
                    (wid,),
                ).fetchall()
            )
            assert any(r["status"] == "capped_out" for r in caps)
            row = database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop"))
            assert row["count"] == 3  # attempts + 1, the breaching bump
        finally:
            await database.close()

    asyncio.run(scenario())


def test_fix_loop_wall_clock_breach(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "noop")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    # executor.check() reads the current time via executor._now (a seam re-exported
    # from store). Pin it far in the future so the very first breach check trips on
    # elapsed wall-clock, regardless of the (large) attempts cap.
    monkeypatch.setattr("kraft.executor._now", lambda: "2099-01-01T00:00:00+00:00")

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = _registry()
            pol = _make_policy(tmp_path, attempts=99, wall_clock_s=1)
            wid = await executor.intake(
                database, rd, title="slow",
                repo=str(repo), template=_fixloop_template(), bd_cwd=str(tracker),
            )
            result = await executor.run(
                database, rd, work_item_id=wid, registry=registry,
                bd_cwd=str(tracker), policy=pol,
            )
            assert result == "needs_human"
            types = _types(database, wid)
            assert types.count("fix_cycle_started") == 0  # breached before any fix task
            row = database.read(lambda c: store.read_counter(c, wid, "verify_fix_loop"))
            assert row["count"] == 1  # one bump, then the breach check
        finally:
            await database.close()

    asyncio.run(scenario())
```

The wall-clock test relies on a seam: `executor.py` does `from kraft.store import _now as _now` at module level and calls `_now()` for the `policy.check` timestamp (Step 4). `store.bump_counter` still uses `store._now()` directly for `started_at`, so `started_at` is real and `_now()` (patched to 2099) is far past it → first check breaches. `fix_cycle_started` is emitted only *after* a passing breach check, so a wall-clock breach on the first check yields zero `fix_cycle_started` events and `count == 1`.

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_fix_loop.py -v`
Expected: FAIL — `executor.run` has no `policy` kwarg; `fix_cycle_started` never emitted; a failing `verify` immediately → `needs_human` with no counter row.

- [ ] **Step 3: Add `instruction_override` to `_dispatch`**

In `src/kraft/executor.py`, change `_dispatch`'s signature and the `agent` branch:

```python
async def _dispatch(
    db, run_dirs, task_hook, node, work_item_row, registry, worktree,
    *, instruction_override: str | None = None
) -> str:
    ...
    if kind == "agent":
        return await _agent.run_agent_task(
            db, run_dirs,
            hook_point=task_hook,
            command=binding["command"],
            title=work_item_row["title"],
            task_instruction=instruction_override or work_item_row["title"],
            repo_path=work_item_row["repo"],
            cwd=worktree,
            **common,
        )
```

Every other call site of `_dispatch` stays as-is (kwarg defaulted).

- [ ] **Step 4: Extract `_measure_node`, add the fix-loop branch, thread `policy`**

In `src/kraft/executor.py`:

Add near the top (module level):

```python
from kraft.store import _now as _now  # test seam for wall-clock checks

_FIX_PROMPT = (
    "The checks in node {node_id} failed for this work item. Fix the code so they "
    "pass. Make no unrelated changes. Failing hook points: {failed}"
)
```

Replace the current `_walk_node` with:

```python
async def _measure_node(
    db, run_dirs, work_item_id, node, row, registry, worktree
) -> tuple[str, list[str]]:
    await db.write(lambda c, node=node: store.enter_node(c, work_item_id, node["id"]))
    tasks = node["tasks"]
    results = await asyncio.gather(
        *(_dispatch(db, run_dirs, t, node, row, registry, worktree) for t in tasks),
        return_exceptions=True,
    )
    failed = [
        tasks[i]
        for i, r in enumerate(results)
        if isinstance(r, BaseException) or r == "failed"
    ]
    if failed:
        return "failed", failed
    return "ok", []


async def _walk_node(
    db, run_dirs, work_item_id, node, row, registry, worktree, *, policy=None
) -> str:
    key = node.get("fix_loop")

    if not key:
        verdict, failed = await _measure_node(
            db, run_dirs, work_item_id, node, row, registry, worktree
        )
        if verdict == "failed":
            reason = f"task failed in node {node['id']}: {failed}"
            await db.write(
                lambda c: store.mark_needs_human(c, work_item_id, node["id"], reason)
            )
            return "needs_human"
        await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
        return "ok"

    if policy is None:
        raise RuntimeError(f"node {node['id']!r} has fix_loop but no policy was provided")

    from kraft import policy as _policy

    cap = _policy.resolve_cap(policy, key)
    while True:
        verdict, failed = await _measure_node(
            db, run_dirs, work_item_id, node, row, registry, worktree
        )
        if verdict == "ok":
            await db.write(lambda c: store.complete_node(c, work_item_id, node["id"]))
            return "ok"

        count, started_at = await db.write(
            lambda c: store.bump_counter(c, work_item_id, key, cap)
        )
        if _policy.check(count=count, started_at=started_at, cap=cap, now=_now()) == "breached":
            await db.write(
                lambda c: store.mark_sessions_capped_out(c, work_item_id, node["id"])
            )
            await db.write(
                lambda c: store.mark_needs_human(
                    c, work_item_id, node["id"],
                    f"{key} exhausted after {count - 1} fix cycle(s)",
                )
            )
            return "needs_human"

        await db.write(
            lambda c: events.append(
                c, work_item_id, "fix_cycle_started",
                {"node_id": node["id"], "cycle": count, "failed_tasks": failed},
            )
        )
        await _dispatch(
            db, run_dirs, "on.implementation.start", node, row, registry, worktree,
            instruction_override=_FIX_PROMPT.format(node_id=node["id"], failed=", ".join(failed)),
        )
        # fix task status is not branched on; loop re-measures
```

Confirm `events` is imported in `executor.py` (it is not currently — add `from kraft import events`). Confirm `store` is imported (it is).

Thread `policy` through `run` and `resume`:

- `run(...)` signature: add `policy=None` keyword-only. Pass `policy=policy` into every `_walk_node(...)` call inside `run`.
- `resume(...)` signature: add `policy=None` keyword-only. Pass `policy=policy` into `_reconcile_current_node(...)`, and from there into its `_walk_node(...)` calls. Also pass into the tail-loop `_walk_node(...)` calls in `resume`.
- `_reconcile_current_node(...)` signature: add `policy=None` keyword-only; forward to its `_walk_node` calls.

- [ ] **Step 5: Run the fix-loop tests**

Run: `uv run pytest tests/test_fix_loop.py -v`
Expected: PASS. If the wall-clock test is flaky, switch it to the `_now` monkeypatch seam described in Step 1's note.

- [ ] **Step 6: Run the executor / resume / gate regression**

Run: `uv run pytest tests/test_executor.py tests/test_resume.py tests/test_gates.py -v`
Expected: PASS unchanged — those templates have no `fix_loop`, and `_walk_node`'s non-`fix_loop` path is behaviorally identical (same events: `node_started`, then `node_completed` or `work_item_needs_human`).

- [ ] **Step 7: Full regression + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS + clean.

- [ ] **Step 8: Commit**

```bash
git add src/kraft/executor.py tests/support/harness.py tests/test_fix_loop.py
git commit -m "feat: bounded fix loop for the verify node (backward-motion entry A)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 5: `resume` honors an already-approved gate (Kraft-2v0)

**Files:**
- Modify: `src/kraft/executor.py`
- Test: `tests/test_resume.py`

**Interfaces:**
- Consumes: `events.read_after` (exists); `store.mark_completed`, `beads.complete` (exist).
- Produces:
  - `executor._gate_cleared(db, work_item_id: str, gate: str) -> bool` — reads the item's events, walks backward; `True` iff the first `gate_*` event encountered (`gate_requested` / `gate_approved` / `gate_rejected`) is `gate_approved` with `payload["gate"] == gate`.
  - `executor.resume(...)` — when `nodes[start]` has a `gate_after` that `_gate_cleared` reports cleared, it advances past that node instead of reconciling + re-requesting the gate.

- [ ] **Step 1: Write the failing test**

In `tests/test_resume.py`, add (uses the `default` template — add a `_default_template()` helper mirroring `test_gates.py`'s if not present):

```python
def _default_template():
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["default"]


def test_resume_after_gate_approval_does_not_re_request_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database, rd, title="make the failing test pass",
                repo=str(repo), template=_default_template(), bd_cwd=str(tracker),
            )
            # walk to the spec gate
            r = await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker),
            )
            assert r == "awaiting_gate"
            # approve it, then simulate a crash BEFORE the approve endpoint's run() spawns
            await database.write(lambda c: store.approve_gate(c, wid, "spec_approval"))
            wi = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id=?", (wid,)
                ).fetchone()
            )
            assert wi["status"] == "active" and wi["current_node_id"] == "spec"

            before = _types(database, wid)
            r2 = await executor.resume(
                database, rd, work_item_id=wid, registry=registry,
                adopted={}, bd_cwd=str(tracker),
            )
            after = _types(database, wid)
            new_events = after[len(before):]
            # the spec gate is NOT re-requested; the walk moves on to the plan gate
            assert "gate_requested" in new_events  # for plan_approval
            plan_gate = [
                e for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "gate_requested"
            ][-1]
            assert plan_gate["payload"]["gate"] == "plan_approval"
            # no duplicate node_completed for spec
            spec_completions = [
                e for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "node_completed" and e["payload"]["node_id"] == "spec"
            ]
            assert len(spec_completions) == 1
            assert r2 == "awaiting_gate"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_before_gate_approval_still_re_requests_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database, rd, title="t",
                repo=str(repo), template=_default_template(), bd_cwd=str(tracker),
            )
            await executor.run(
                database, rd, work_item_id=wid, registry=registry, bd_cwd=str(tracker),
            )
            # NOT approved. Force status back to active as a crash-mid-await would look?
            # No — a genuine await leaves status=needs_human, which reattach does not
            # resume. This case only matters if something set active without approving.
            # Simulate that pathological state:
            await database.write(
                lambda c: c.execute(
                    "UPDATE work_items SET status='active' WHERE id=?", (wid,)
                )
            )
            r = await executor.resume(
                database, rd, work_item_id=wid, registry=registry,
                adopted={}, bd_cwd=str(tracker),
            )
            assert r == "awaiting_gate"
            last_gate = [
                e for e in database.read(lambda c: events.read_after(c, 0, wid))
                if e["type"] == "gate_requested"
            ][-1]
            assert last_gate["payload"]["gate"] == "spec_approval"
        finally:
            await database.close()

    asyncio.run(scenario())
```

Add imports to `tests/test_resume.py` if missing: `from kraft.templates import load_registry, load_templates` (already imported), and a module-level `_REPO_ROOT` / `_types` (already present as `_types`).

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_resume.py -k gate -v`
Expected: FAIL — `resume` reconciles the `spec` node, re-emits `node_completed`, and `_maybe_gate` re-requests `spec_approval` (so the last `gate_requested` is `spec_approval`, not `plan_approval`).

- [ ] **Step 3: Add `_gate_cleared` and the gate-skip in `resume`**

In `src/kraft/executor.py`:

```python
def _gate_cleared(db, work_item_id: str, gate: str) -> bool:
    evts = db.read(lambda c: events.read_after(c, 0, work_item_id))
    for e in reversed(evts):
        if e["type"] in ("gate_requested", "gate_approved", "gate_rejected"):
            return e["type"] == "gate_approved" and e["payload"].get("gate") == gate
    return False
```

In `resume(...)`, after `start = next(i for i, n in enumerate(nodes) if n["id"] == cur)` and before the `_reconcile_current_node(...)` call, add:

```python
    node0 = nodes[start]
    gate0 = node0.get("gate_after")
    if gate0 and _gate_cleared(db, work_item_id, gate0):
        # the gate was approved before the crash; do not reconcile or re-request it.
        start += 1
        if start >= len(nodes):
            await db.write(lambda c: store.mark_completed(c, work_item_id))
            try:
                await beads.complete(row["bead_id"], cwd=bd_cwd)
            except Exception as exc:  # noqa: BLE001
                logger.warning("bead close failed for %s: %r", row["bead_id"], exc)
            return "completed"
        # fall through: reconcile from the post-gate node instead
```

Then change the reconciliation + first-gate lines that follow so they operate on `nodes[start]` (they currently use `nodes[start]` already — verify the variable, not a stale `node0`), and ensure the `_maybe_gate(db, work_item_id, nodes[start])` call that runs after `_reconcile_current_node` is **not** reached for the node we just skipped. Since `start` now points at the post-gate node, `_maybe_gate(nodes[start])` correctly evaluates that node's own `gate_after` (usually `None`) — no extra guard needed.

Thread `policy` (from Task 4) into the `_reconcile_current_node` / `_walk_node` calls here too if not already done.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_resume.py -v`
Expected: PASS (all — new gate tests + existing quick-task resume tests)

- [ ] **Step 5: Full regression + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS + clean.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/executor.py tests/test_resume.py
git commit -m "fix: resume honors a gate approved just before a crash (Kraft-2v0)

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

## Task 6: API wire-up — load `policy`, thread it, surface `/health`

**Files:**
- Modify: `src/kraft/api.py`
- Modify: `tests/support/harness.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `policy.load_policy`, `policy.PolicyError` (Task 1); `executor.run` / `resume` with `policy=` (Tasks 4, 5).
- Produces:
  - `app.state.policy: policy.Policy | None` — `None` when `policy.yaml` failed to load.
  - `app.state.invalid_policy: list[str]` — `[]` or `[<reason>]`.
  - Every `executor.run(...)` / `executor.resume(...)` spawn in `api.py` passes `policy=st.policy`.
  - `GET /health` response gains `"invalid_policy": [...]`; `"status"` is `"degraded"` when either `invalid_templates` or `invalid_policy` is non-empty.

- [ ] **Step 1: Extend `fake_templates_dir` to ship a `policy.yaml`**

In `tests/support/harness.py`, at the end of `fake_templates_dir` (before `return d`):

```python
    shutil.copy(_REPO_ROOT / "templates" / "policy.yaml", d / "policy.yaml")
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_api.py`, add:

```python
def test_health_reports_invalid_policy(tmp_path, monkeypatch):
    # point the templates dir at a copy with a broken policy.yaml
    ...  # mirror the existing test_health_ok_and_degraded setup
    # write templates_dir / "policy.yaml" with "default: { attempts: 0, wall_clock_s: 1 }"
    with _client(tmp_path, monkeypatch) as client:
        h = client.get("/health").json()
        assert h["status"] == "degraded"
        assert h["invalid_policy"]


def test_health_ok_with_valid_policy(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        h = client.get("/health").json()
        assert h["invalid_policy"] == []


def test_default_chain_fix_loop_over_http(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"title": "make the failing test pass", "repo": str(repo),
                  "chain_template": "default"},
        ).json()["id"]
        # approve all four gates in order; the verify node's fix loop runs between
        # chain_finalized and human_review_approval.
        for gate in ("spec_approval", "plan_approval", "chain_finalized",
                     "human_review_approval"):
            _poll_events(client, wid, "gate_requested")
            r = client.post(f"/work-items/{wid}/gates/{gate}/approve")
            assert r.status_code == 200, r.text
        _poll_events(client, wid, "work_item_completed")
        evts = client.get(f"/work-items/{wid}/events").json()
        assert any(e["type"] == "fix_cycle_started" for e in evts)
```

Check `tests/test_api.py` for the existing `_client`, `_poll_events`, and the health test's plumbing for pointing at a custom templates dir; reuse them. If pointing `_client` at a broken-policy dir is awkward, split `test_health_reports_invalid_policy` to build its own `_client` variant like the existing degraded-health test does.

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_api.py -k "policy or fix_loop" -v`
Expected: FAIL — `/health` has no `invalid_policy` key; the default-chain run raises inside `_walk_node` ("fix_loop but no policy provided") because `executor.run` is spawned without `policy=`.

- [ ] **Step 4: Load and thread `policy` in `api.py`**

In `src/kraft/api.py` `lifespan`, after `registry = load_registry(...)`:

```python
    from kraft import policy as _policy

    policy_obj = None
    invalid_policy: list[str] = []
    try:
        policy_obj = _policy.load_policy(templates_dir / "policy.yaml")
    except _policy.PolicyError as exc:
        invalid_policy = [str(exc)]
```

Store on state:

```python
    app.state.policy = policy_obj
    app.state.invalid_policy = invalid_policy
```

Pass `policy=policy_obj` into the `executor.resume(...)` call in the `summary.resumed_work_items` loop.

In `create_work_item`, the `approve_gate` endpoint, and anywhere else `executor.run` / `executor.resume` is spawned, add `policy=st.policy` to the call.

In `GET /health`:

```python
@app.get("/health")
async def health(request: Request):
    st = request.app.state
    invalid = st.templates.invalid
    invalid_policy = st.invalid_policy
    return {
        "status": "degraded" if (invalid or invalid_policy) else "ok",
        "invalid_templates": invalid,
        "invalid_policy": invalid_policy,
        "reattach_summary": asdict(st.reattach_summary),
    }
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_api.py -v`
Expected: PASS (all — new + existing)

- [ ] **Step 6: Full regression + lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS (baseline count + all new tests; 1 pre-existing skip) + clean.

- [ ] **Step 7: Commit**

```bash
git add src/kraft/api.py tests/support/harness.py tests/test_api.py
git commit -m "feat: load policy.yaml, thread it into the executor, surface in /health

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

- [ ] **Step 8: Close the beads**

```bash
bd close Kraft-ue7 Kraft-2v0 --reason="Policy engine + verify fix loop shipped; resume honors pre-crash gate approval"
```

Then report handoff: changed files, `uv run pytest -q` result, `git log --oneline` since `a9c3723`.

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task |
|---|---|
| §1 `src/kraft/policy.py` | Task 1 |
| §1 `templates/policy.yaml` | Task 1 |
| §1 `db.py` schema + migration | Task 2 |
| §1 `store.py` `bump_counter` / `read_counter` / `mark_sessions_capped_out` | Task 2 |
| §1 `templates.py` `fix_loop` validation | Task 3 |
| §1 `executor.py` fix-loop branch + `policy` threading | Task 4 |
| §1 `executor.py` `resume` gate-skip | Task 5 |
| §1 `api.py` load policy + `/health` | Task 6 |
| §1 `templates/default.yaml` `verify` `fix_loop` | Task 3 |
| §2.A `policy.py` shape | Task 1 |
| §2.B `policy.yaml` + no override | Task 1 |
| §2.C `retry_counters` snapshot semantics | Task 2 (`bump_counter`) |
| §2.D forward-only migration runner | Task 2 |
| §2.E `fix_loop` node key + validation | Task 3 |
| §2.F "non-clean" = `"failed"`, fix status not branched | Task 4 (`_measure_node`, loop) |
| §2.G approve-then-crash / `_gate_cleared` | Task 5 |
| §3 `policy.yaml` content | Task 1 Step 3 |
| §4 schema + migration | Task 2 |
| §5 `policy.py` | Task 1 |
| §6.1 thread `policy` | Task 4 Step 4 |
| §6.2 `_walk_node` fix-loop branch + `_measure_node` | Task 4 Step 4 |
| §6.3 `mark_sessions_capped_out` | Task 2 Step 5 |
| §6.4 `resume` passes `policy` | Task 4 Step 4 / Task 5 Step 3 |
| §6.5 `instruction_override` + `_FIX_PROMPT` | Task 4 Steps 3–4 |
| §7 `resume` gate-skip + `_gate_cleared` | Task 5 |
| §8 `fix_cycle_started` event | Task 4 Step 4 |
| §8 `/health` `invalid_policy` | Task 6 |
| §9 error table | Tasks 4 (breach paths), 6 (malformed policy) |
| §10 testing | every task's test steps; `test_fix_loop.py` Task 4, `test_resume.py` Task 5 |
| §11 gaps | not implemented by design |
| §12 build order | Tasks 1→6 follow it |

**2. Placeholder scan:** No "TBD" / "handle edge cases" / "similar to Task N". Every code step has literal code. Soft spots, both with concrete guidance given: Task 3 Step 1 (reuse `_dir`/`REGISTRY_YAML` — "mirror the helper the `gate_after` tests use" if absent); Task 4 Step 1 wall-clock test (a `_now` monkeypatch seam is specified in Step 4 — `from kraft.store import _now as _now` in `executor.py`, patch `kraft.executor._now`); Task 6 Steps 1–2 (`_client` / `_poll_events` reuse — "mirror the existing degraded-health test").

**3. Type consistency:**
- `policy.Cap(attempts, wall_clock_s)` / `policy.Policy(loops, default)` — defined Task 1, used Task 2 (`bump_counter(... cap)` reads `cap.attempts`, `cap.wall_clock_s`), Task 4 (`resolve_cap`, `check`). Consistent.
- `policy.check(*, count, started_at, cap, now) -> str` — defined Task 1, called Task 4 Step 4 with exactly those kwargs. Consistent.
- `store.bump_counter(conn, work_item_id, key, cap) -> tuple[int, str]` — defined Task 2, called Task 4 (`count, started_at = await db.write(...)`). Consistent.
- `store.mark_sessions_capped_out(conn, work_item_id, node_id)` — defined Task 2, called Task 4. Consistent.
- `executor._measure_node(...) -> tuple[str, list[str]]` — defined Task 4, called only within `_walk_node` (same task). Consistent.
- `executor._walk_node(..., *, policy=None)` — signature change Task 4; existing direct callers in `test_resume.py` / `test_gates.py` pass 7 positional args and no `policy`, still valid (kwarg-only, defaulted). Consistent.
- `executor._dispatch(..., *, instruction_override=None)` — Task 4; every existing caller omits it. Consistent.
- `executor._gate_cleared(db, work_item_id, gate) -> bool` — defined + called within Task 5.
- `executor.run` / `executor.resume` gain `policy=None` — Task 4; `api.py` passes `policy=st.policy` — Task 6. Test call sites that omit it get `None` and only hit the `raise` if they walk a `fix_loop` node (none of the non-fix-loop tests do). Consistent.

No gaps found.
