# Skeleton Chunk C — Resilience + API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the test-driven execution core into a running HTTP orchestrator that survives its own crash and reattaches detached worker processes on restart.

**Architecture:** A FastAPI app on `127.0.0.1` with a lifespan that opens the DB, validates templates, runs a reattach scan, then serves. `POST /work-items` spawns an in-memory `asyncio.Task` running the existing `executor.run`. On restart, `kraft.reattach` scans non-terminal `worker_sessions`, does a psutil PID + `create_time` identity check, adopts live processes / resolves from result files / marks the rest `unknown`, then a new `executor.resume` entrypoint continues each interrupted chain from `current_node_id`.

**Tech Stack:** Python 3.14, FastAPI, uvicorn, SQLite (WAL, single-writer queue), psutil, pytest (no `pytest-asyncio` — tests wrap an inner coroutine in `asyncio.run`; API tests use `fastapi.testclient.TestClient`).

**Spec:** `docs/superpowers/specs/2026-09-01-skeleton-c-resilience-api.md`

## Global Constraints

- **Python 3.14+** (`requires-python = ">=3.14"`). `from __future__ import annotations` at the top of every module.
- **New runtime deps allowed this chunk:** `fastapi`, `uvicorn`. **Dev-only:** `httpx` (pulled in by `TestClient`; add explicitly). No other new dependencies — reach for stdlib first.
- **No schema migration.** `worker_sessions.status` CHECK already allows `'unknown'`. `SCHEMA_VERSION` stays `1`.
- **DB access rule:** every row mutation + its event go through one `db.write(fn)` call (the `fn` does both, the writer commits once). Reads via `db.read(fn)`. Never open your own sqlite connection.
- **Bind `127.0.0.1` only. No auth.** (`02` §9 engages auth on LAN bind only — not in scope.)
- **Runtime config via env vars:** `KRAFT_RUN_DIR` (default `.kraft-run`), `KRAFT_PORT` (default `8765`), `KRAFT_BD_CWD` (default: inherit cwd → `None`), `KRAFT_TEMPLATES_DIR` (default: repo `templates/`).
- **Registry is reconstructed from `templates/registry.yaml` at startup**, never persisted on the work-item row.
- **Ruff** is the linter (`select = ["E","F","W","I","UP","B"]`, line-length 100). Run `uv run ruff check` before every commit.
- **Test command:** `uv run pytest -q` (hermetic gate — must stay green, no `e2e` collected). Chunk A+B currently have ~40 passing tests; do not break them.
- **`e2e` tests** are marked `@pytest.mark.e2e` and skipped unless `KRAFT_E2E=1` **and** `claude` is on `PATH`.
- Conservative git profile: commit per task (below), do **not** push, do **not** merge to `main`.

---

## File Structure

**Create:**
- `src/kraft/reattach.py` — startup scan, identity check, adopt/resolve/unknown, `ReattachSummary`.
- `src/kraft/api.py` — FastAPI `app`, lifespan, 5 endpoints.
- `src/kraft/__main__.py` — `python -m kraft` → uvicorn.
- `fixtures/fake-claude.sh` — controllable fake agent, 3 modes, always writes `$KRAFT_RESULT_PATH`.
- `tests/support/server.py` — launch `python -m kraft` as a subprocess, wait for `/health`, yield an `httpx.Client` + the process handle.
- `tests/test_reattach.py` — unit tests for `kraft.reattach`.
- `tests/test_resume.py` — unit tests for `executor.resume`.
- `tests/test_api.py` — hermetic API tests (`TestClient`).
- `tests/test_api_reattach.py` — subprocess-server reattach tests.
- `tests/test_e2e.py` — gated real-`claude` happy path.

**Modify:**
- `src/kraft/store.py` — add `session_unknown`, `session_reattached` helpers.
- `src/kraft/executor.py` — extract `_walk_node`; add `resume` + `_reconcile_current_node`. `run`'s observable behaviour unchanged.
- `tests/support/harness.py` — add `fake_templates_dir(tmp_path, agent_command)` helper.
- `pyproject.toml` — add `fastapi`, `uvicorn` to `dependencies`; `httpx` to `[dependency-groups].dev`.
- `tests/support/conftest.py` — add the `e2e` skip logic (or a root `tests/conftest.py` — see Task 9).

**Responsibilities:**
- `reattach.py` — knows nothing about HTTP. Pure `(db, run_dirs, registry) -> (ReattachSummary, adopted_tasks)`. Testable with a hand-seeded DB.
- `executor.resume` — knows nothing about HTTP or reattach internals; takes an `adopted: dict[session_id, Task]` map.
- `api.py` — wiring only: lifespan composes `Database.open` + `load_registry`/`load_templates` + `reattach` + `executor.resume`/`run`; endpoints are thin DB reads.
- `__main__.py` — 3 lines; exists so tests get a killable process.

---

## Task 1: `store.session_unknown` + `store.session_reattached`

**Files:**
- Modify: `src/kraft/store.py` (append two functions after `session_exited`, ~line 117)
- Test: `tests/test_store.py` (append two tests)

**Interfaces:**
- Consumes: `kraft.events.append(conn, work_item_id, type, payload) -> int`; existing `store._now()`.
- Produces:
  - `store.session_unknown(conn: sqlite3.Connection, session_id: str) -> None` — sets the session row `status='unknown'`, `exited_at=now`; emits event `session_unknown` `{"session_id": session_id}` on the row's `work_item_id`.
  - `store.session_reattached(conn: sqlite3.Connection, session_id: str) -> None` — **no row change**; emits event `session_reattached` `{"session_id": session_id, "pid": <row.pid>}` on the row's `work_item_id`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_session_unknown_sets_status_and_event(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c, id="s1", work_item_id="w1", node_id="env_setup",
                    hook_point="on.env.prepare", log_path="/l", result_path="/r",
                )
            )
            await database.write(lambda c: store.session_unknown(c, "s1"))
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, exited_at FROM worker_sessions WHERE id='s1'"
                ).fetchone()
            )
            assert row["status"] == "unknown"
            assert row["exited_at"] is not None
            ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev["type"] == "session_unknown"
            assert ev["payload"] == {"session_id": "s1"}
        finally:
            await database.close()

    asyncio.run(scenario())


def test_session_reattached_emits_event_without_row_change(tmp_path):
    async def scenario():
        database = await _open(tmp_path)
        try:
            await _mk_item(database)
            await database.write(
                lambda c: store.create_session(
                    c, id="s1", work_item_id="w1", node_id="env_setup",
                    hook_point="on.env.prepare", log_path="/l", result_path="/r",
                )
            )
            await database.write(lambda c: store.session_running(c, "s1", 4321, 111.5))
            await database.write(lambda c: store.session_reattached(c, "s1"))
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["status"] == "running"  # unchanged
            ev = database.read(lambda c: events.read_after(c, 0, "w1"))[-1]
            assert ev["type"] == "session_reattached"
            assert ev["payload"] == {"session_id": "s1", "pid": 4321}
        finally:
            await database.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_store.py::test_session_unknown_sets_status_and_event tests/test_store.py::test_session_reattached_emits_event_without_row_change -q`
Expected: FAIL — `AttributeError: module 'kraft.store' has no attribute 'session_unknown'`

- [ ] **Step 3: Implement**

Append to `src/kraft/store.py`:

```python
def session_unknown(conn: sqlite3.Connection, session_id) -> None:
    conn.execute(
        "UPDATE worker_sessions SET status = 'unknown', exited_at = ? WHERE id = ?",
        (_now(), session_id),
    )
    row = conn.execute(
        "SELECT work_item_id FROM worker_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    events.append(conn, row["work_item_id"], "session_unknown", {"session_id": session_id})


def session_reattached(conn: sqlite3.Connection, session_id) -> None:
    row = conn.execute(
        "SELECT work_item_id, pid FROM worker_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    events.append(
        conn,
        row["work_item_id"],
        "session_reattached",
        {"session_id": session_id, "pid": row["pid"]},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_store.py -q`
Expected: PASS (all store tests)

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/kraft/store.py tests/test_store.py
git add src/kraft/store.py tests/test_store.py
git commit -m "feat(store): session_unknown and session_reattached helpers"
```

---

## Task 2: Extract `executor._walk_node` (refactor, no behaviour change)

**Files:**
- Modify: `src/kraft/executor.py:83-126` (the `run` function body)
- Test: `tests/test_executor.py` (unchanged — it is the regression guard)

**Interfaces:**
- Consumes: existing `store.enter_node`, `store.complete_node`, `store.mark_needs_human`, `executor._dispatch`.
- Produces:
  - `executor._walk_node(db, run_dirs, work_item_id: str, node: dict, row, registry: Registry, worktree) -> str` — returns `'ok'` or `'needs_human'`. Emits `node_started` (via `enter_node`), `asyncio.gather`s `_dispatch` over `node["tasks"]` (with `return_exceptions=True`), and on any exception or `'failed'` result writes `mark_needs_human` and returns `'needs_human'`; otherwise writes `complete_node` and returns `'ok'`.
  - `run` keeps its signature `run(db, run_dirs, *, work_item_id: str, registry: Registry, bd_cwd: str | None = None) -> str` and its return values `'completed' | 'needs_human'`.

- [ ] **Step 1: Confirm the regression guard passes now**

Run: `uv run pytest tests/test_executor.py -q`
Expected: PASS (6 tests). If not, stop — the baseline is broken.

- [ ] **Step 2: Refactor `run` to use a new `_walk_node`**

Replace the body of `run` (`src/kraft/executor.py`, from `await db.write(lambda c: store.load_chain(...))` through the final `return "completed"`) and insert `_walk_node` above `run`:

```python
async def _walk_node(
    db, run_dirs, work_item_id: str, node: dict, row, registry: Registry, worktree
) -> str:
    await db.write(lambda c, node=node: store.enter_node(c, work_item_id, node["id"]))
    results = await asyncio.gather(
        *(
            _dispatch(db, run_dirs, task, node, row, registry, worktree)
            for task in node["tasks"]
        ),
        return_exceptions=True,
    )
    excs = [r for r in results if isinstance(r, BaseException)]
    if excs or "failed" in results:
        reason = f"task failed in node {node['id']}"
        if excs:
            reason += ": " + ", ".join(repr(e) for e in excs)
        await db.write(
            lambda c, node=node, reason=reason: store.mark_needs_human(
                c, work_item_id, node["id"], reason
            )
        )
        return "needs_human"
    await db.write(lambda c, node=node: store.complete_node(c, work_item_id, node["id"]))
    return "ok"


async def run(
    db, run_dirs, *, work_item_id: str, registry: Registry, bd_cwd: str | None = None
) -> str:
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")
    chain = json.loads(row["chain_definition"])
    nodes = chain["nodes"]
    worktree = run_dirs.worktrees / work_item_id

    await db.write(lambda c: store.load_chain(c, work_item_id, nodes[0]["id"]))

    for node in nodes:
        if await _walk_node(db, run_dirs, work_item_id, node, row, registry, worktree) == "needs_human":
            return "needs_human"

    await db.write(lambda c: store.mark_completed(c, work_item_id))
    try:
        await beads.complete(row["bead_id"], cwd=bd_cwd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bead close failed for %s: %r", row["bead_id"], exc)
    return "completed"
```

- [ ] **Step 3: Run the regression guard**

Run: `uv run pytest tests/test_executor.py -q`
Expected: PASS (same 6 tests, unchanged)

- [ ] **Step 4: Full hermetic suite**

Run: `uv run pytest -q`
Expected: PASS (all Chunk A+B tests)

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check src/kraft/executor.py
git add src/kraft/executor.py
git commit -m "refactor(executor): extract _walk_node from run"
```

---

## Task 3: `executor.resume` + `_reconcile_current_node`

**Files:**
- Modify: `src/kraft/executor.py` (add two functions after `run`)
- Test: `tests/test_resume.py` (create)

**Interfaces:**
- Consumes: `_walk_node` (Task 2), `store.load_chain`, `store.complete_node`, `store.mark_needs_human`, `store.mark_completed`, `beads.complete`, `_dispatch`.
- Produces:
  - `executor.resume(db, run_dirs, *, work_item_id: str, registry: Registry, adopted: dict[str, "asyncio.Task"], bd_cwd: str | None = None) -> str` — returns `'completed' | 'needs_human'`. Starts at `work_items.current_node_id` (does **not** call `load_chain` unless `current_node_id is None`), reconciles the current node against existing `worker_sessions` rows, then walks the remaining nodes exactly like `run`.
  - `executor._reconcile_current_node(db, run_dirs, work_item_id, node, row, registry, worktree, adopted) -> str` — `'ok' | 'needs_human'`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_resume.py`:

```python
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from support.harness import fake_registry, isolated_bd, make_repo

from kraft import db, events, executor, store
from kraft.paths import RunDirs
from kraft.templates import Template, load_registry, load_templates

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_AGENT = Path(__file__).parent / "support" / "fake_agent.py"


def _quick_task() -> Template:
    reg = load_registry(_REPO_ROOT / "templates" / "registry.yaml")
    return load_templates(_REPO_ROOT / "templates", reg).valid["quick-task"]


def _types(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def test_resume_from_verify_with_env_and_impl_done(tmp_path, monkeypatch):
    """Chain crashed after `implementation` completed; resume runs only `verify`."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database, rd, title="make the failing test pass",
                repo=str(repo), template=_quick_task(), bd_cwd=str(tracker),
            )
            # Simulate a partial run: env_setup + implementation done, stopped before verify.
            await database.write(lambda c: store.load_chain(c, wid, "env_setup"))
            # do the real env_setup + implementation via run's node walker
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            wt = rd.worktrees / wid
            assert await executor._walk_node(database, rd, wid, {"id": "env_setup", "tasks": ["on.env.prepare"]}, row, registry, wt) == "ok"
            assert await executor._walk_node(database, rd, wid, {"id": "implementation", "tasks": ["on.implementation.start"]}, row, registry, wt) == "ok"
            # current_node_id now points at implementation (last enter_node). Move it to verify
            # the way a crash-recovery would NOT — instead leave it and call resume, which
            # should see implementation's session done and advance.
            result = await executor.resume(
                database, rd, work_item_id=wid, registry=registry, adopted={}, bd_cwd=str(tracker),
            )
            assert result == "completed"
            assert "a + b" in (wt / "calc.py").read_text()
            t = _types(database, wid)
            assert t[-1] == "work_item_completed"
            # verify ran exactly once
            assert t.count("node_started") == 3
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_no_session_for_current_node_dispatches_fresh(tmp_path, monkeypatch):
    """Crash between enter_node(verify) and its first create_session -> resume re-dispatches verify."""
    monkeypatch.delenv("KRAFT_FAKE_AGENT", raising=False)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            registry = fake_registry(sys.executable, _FAKE_AGENT)
            wid = await executor.intake(
                database, rd, title="make the failing test pass",
                repo=str(repo), template=_quick_task(), bd_cwd=str(tracker),
            )
            row = database.read(
                lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
            )
            wt = rd.worktrees / wid
            await database.write(lambda c: store.load_chain(c, wid, "env_setup"))
            await executor._walk_node(database, rd, wid, {"id": "env_setup", "tasks": ["on.env.prepare"]}, row, registry, wt)
            await executor._walk_node(database, rd, wid, {"id": "implementation", "tasks": ["on.implementation.start"]}, row, registry, wt)
            # hand-advance current_node_id to verify with NO session rows for it
            await database.write(lambda c: store.enter_node(c, wid, "verify"))
            result = await executor.resume(
                database, rd, work_item_id=wid, registry=registry, adopted={}, bd_cwd=str(tracker),
            )
            assert result == "completed"
            sessions = database.read(
                lambda c: c.execute(
                    "SELECT node_id FROM worker_sessions WHERE work_item_id = ? AND node_id = 'verify'",
                    (wid,),
                ).fetchall()
            )
            assert len(sessions) == 1
        finally:
            await database.close()

    asyncio.run(scenario())


def test_resume_current_node_failed_session_is_needs_human(tmp_path):
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)

    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            from kraft.templates import Registry

            registry = Registry(hooks={
                "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                "on.implementation.start": {"kind": "agent", "command": "unused"},
                "on.test.run": {"kind": "subprocess", "command": ["true"]},
            })
            wid = await executor.intake(
                database, rd, title="t", repo=str(repo), template=_quick_task(), bd_cwd=str(tracker),
            )
            await database.write(lambda c: store.load_chain(c, wid, "implementation"))
            await database.write(lambda c: store.enter_node(c, wid, "implementation"))
            # a session row already resolved 'failed'
            await database.write(lambda c: store.create_session(
                c, id="s-impl", work_item_id=wid, node_id="implementation",
                hook_point="on.implementation.start", log_path="/l", result_path="/r",
            ))
            await database.write(lambda c: store.session_exited(c, "s-impl", "failed"))
            result = await executor.resume(
                database, rd, work_item_id=wid, registry=registry, adopted={}, bd_cwd=str(tracker),
            )
            assert result == "needs_human"
            row = database.read(
                lambda c: c.execute(
                    "SELECT status, current_node_id FROM work_items WHERE id = ?", (wid,)
                ).fetchone()
            )
            assert row["status"] == "needs_human"
            assert row["current_node_id"] == "implementation"
        finally:
            await database.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_resume.py -q`
Expected: FAIL — `AttributeError: module 'kraft.executor' has no attribute 'resume'`

- [ ] **Step 3: Implement**

Append to `src/kraft/executor.py`:

```python
async def _reconcile_current_node(
    db, run_dirs, work_item_id, node, row, registry, worktree, adopted
) -> str:
    node_id = node["id"]
    sessions = db.read(
        lambda c: c.execute(
            "SELECT id, status FROM worker_sessions WHERE work_item_id = ? AND node_id = ?",
            (work_item_id, node_id),
        ).fetchall()
    )
    if not sessions:
        # crash landed between enter_node and the first create_session; safe to
        # re-dispatch because current_node_id only advances with node_completed.
        return "ok" if await _walk_node(
            db, run_dirs, work_item_id, node, row, registry, worktree
        ) == "ok" else "needs_human"

    for s in sessions:
        task = adopted.get(s["id"])
        if task is not None:
            await task

    final = db.read(
        lambda c: c.execute(
            "SELECT status FROM worker_sessions WHERE work_item_id = ? AND node_id = ?",
            (work_item_id, node_id),
        ).fetchall()
    )
    # ponytail: single-task-node resume only. A crash mid-fan-out of a multi-task
    # node (fewer sessions than tasks, none failed) -> needs_human, no partial
    # re-dispatch. Upgrade with per-task session reconciliation if multi-task
    # nodes ship.
    if len(final) == len(node["tasks"]) and all(r["status"] == "done" for r in final):
        await db.write(lambda c: store.complete_node(c, work_item_id, node_id))
        return "ok"
    await db.write(
        lambda c: store.mark_needs_human(
            c, work_item_id, node_id, "resume: current-node session did not resolve cleanly"
        )
    )
    return "needs_human"


async def resume(
    db,
    run_dirs,
    *,
    work_item_id: str,
    registry: Registry,
    adopted: dict,
    bd_cwd: str | None = None,
) -> str:
    row = db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (work_item_id,)).fetchone()
    )
    if row is None:
        raise LookupError(f"unknown work_item {work_item_id!r}")
    chain = json.loads(row["chain_definition"])
    nodes = chain["nodes"]
    worktree = run_dirs.worktrees / work_item_id
    cur = row["current_node_id"]

    if cur is None:
        # crash between create_work_item and the first load_chain; nothing ran.
        await db.write(lambda c: store.load_chain(c, work_item_id, nodes[0]["id"]))
        cur = nodes[0]["id"]

    start = next(i for i, n in enumerate(nodes) if n["id"] == cur)

    if await _reconcile_current_node(
        db, run_dirs, work_item_id, nodes[start], row, registry, worktree, adopted
    ) == "needs_human":
        return "needs_human"

    for node in nodes[start + 1 :]:
        if await _walk_node(
            db, run_dirs, work_item_id, node, row, registry, worktree
        ) == "needs_human":
            return "needs_human"

    await db.write(lambda c: store.mark_completed(c, work_item_id))
    try:
        await beads.complete(row["bead_id"], cwd=bd_cwd)
    except Exception as exc:  # noqa: BLE001
        logger.warning("bead close failed for %s: %r", row["bead_id"], exc)
    return "completed"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_resume.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Full suite + lint + commit**

```bash
uv run pytest -q
uv run ruff check src/kraft/executor.py tests/test_resume.py
git add src/kraft/executor.py tests/test_resume.py
git commit -m "feat(executor): resume entrypoint for interrupted chains"
```

---

## Task 4: `kraft.reattach`

**Files:**
- Create: `src/kraft/reattach.py`
- Test: `tests/test_reattach.py` (create)

**Interfaces:**
- Consumes: `db.read` / `db.write`; `store.session_unknown`, `store.session_reattached`, `store.session_exited`, `store.mark_needs_human`; `psutil`.
- Produces:
  - `reattach.ReattachSummary` — `@dataclass` with fields `scanned: int`, `adopted: list[str]`, `resolved_from_file: list[str]`, `unknown: list[str]`, `resumed_work_items: list[str]`.
  - `reattach.reattach(db, run_dirs, registry) -> tuple[ReattachSummary, dict[str, asyncio.Task]]` — the dict is keyed by **session id**, values are background wait tasks for adopted sessions. `registry` is accepted for signature stability (reattach does not use it yet — the resume caller does).
  - `reattach._resolve_file(path: Path) -> str | None` — `'done' | 'failed' | None` (None = no usable file).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_reattach.py`:

```python
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

from kraft import db, events, reattach, store
from kraft.paths import RunDirs
from kraft.templates import Registry

_CHAIN = json.dumps({
    "template_id": "quick-task",
    "nodes": [
        {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
        {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
        {"id": "verify", "tasks": ["on.test.run"], "gate_after": None},
    ],
})
_REG = Registry(hooks={})


async def _seed_item(database, wid="w1"):
    await database.write(lambda c: store.create_work_item(
        c, id=wid, bead_id="B-1", title="t", repo="/r",
        chain_template="quick-task", chain_definition=_CHAIN,
    ))
    await database.write(lambda c: store.load_chain(c, wid, "implementation"))
    await database.write(lambda c: store.enter_node(c, wid, "implementation"))


def _types(database, wid):
    return [e["type"] for e in database.read(lambda c: events.read_after(c, 0, wid))]


def test_pending_session_becomes_unknown_and_needs_human(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed_item(database)
            await database.write(lambda c: store.create_session(
                c, id="s1", work_item_id="w1", node_id="implementation",
                hook_point="on.implementation.start",
                log_path=str(rd.logs / "s1.log"), result_path=str(rd.results / "s1.json"),
            ))  # stays 'pending', pid NULL
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert adopted == {}
            assert summary.unknown == ["s1"]
            assert "w1" not in summary.resumed_work_items
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["status"] == "needs_human"
            assert "session_unknown" in _types(database, "w1")
        finally:
            await database.close()

    asyncio.run(scenario())


def test_running_dead_pid_resolves_from_result_file(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed_item(database)
            (rd.results / "s1.json").write_text('{"status": "done"}')
            await database.write(lambda c: store.create_session(
                c, id="s1", work_item_id="w1", node_id="implementation",
                hook_point="on.implementation.start",
                log_path=str(rd.logs / "s1.log"), result_path=str(rd.results / "s1.json"),
            ))
            # mark it running against a definitely-dead pid with a start time
            await database.write(lambda c: store.session_running(c, "s1", 2_000_000_000, 123.0))
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert adopted == {}
            assert summary.resolved_from_file == ["s1"]
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["status"] == "done"
            t = _types(database, "w1")
            assert "session_reattached" in t and "worker_session_exited" in t
            assert summary.resumed_work_items == ["w1"]
        finally:
            await database.close()

    asyncio.run(scenario())


def test_running_dead_pid_no_file_is_unknown(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        try:
            await _seed_item(database)
            await database.write(lambda c: store.create_session(
                c, id="s1", work_item_id="w1", node_id="implementation",
                hook_point="on.implementation.start",
                log_path=str(rd.logs / "s1.log"), result_path=str(rd.results / "s1.json"),
            ))
            await database.write(lambda c: store.session_running(c, "s1", 2_000_000_000, 123.0))
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert summary.unknown == ["s1"]
            row = database.read(
                lambda c: c.execute("SELECT status FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["status"] == "needs_human"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_live_pid_matching_identity_is_adopted(tmp_path):
    async def scenario():
        rd = RunDirs(tmp_path / "run").ensure()
        database = await db.Database.open(rd.db)
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
        try:
            import psutil

            pst = psutil.Process(proc.pid).create_time()
            await _seed_item(database)
            (rd.results / "s1.json").write_text('{"status": "done"}')
            await database.write(lambda c: store.create_session(
                c, id="s1", work_item_id="w1", node_id="implementation",
                hook_point="on.implementation.start",
                log_path=str(rd.logs / "s1.log"), result_path=str(rd.results / "s1.json"),
            ))
            await database.write(lambda c: store.session_running(c, "s1", proc.pid, pst))
            summary, adopted = await reattach.reattach(database, rd, _REG)
            assert summary.adopted == ["s1"]
            assert set(adopted) == {"s1"}
            assert "session_reattached" in _types(database, "w1")
            # session still running until the child exits
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["status"] == "running"
            # let the adopt task finish
            await adopted["s1"]
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
            )
            assert row["status"] == "done"
        finally:
            proc.wait()
            await database.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_reattach.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.reattach'`

- [ ] **Step 3: Implement `src/kraft/reattach.py`**

```python
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from kraft import store

logger = logging.getLogger(__name__)

_IDENTITY_TOLERANCE_S = 1.0


@dataclass
class ReattachSummary:
    scanned: int = 0
    adopted: list[str] = field(default_factory=list)
    resolved_from_file: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    resumed_work_items: list[str] = field(default_factory=list)


def _resolve_file(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        raw = path.read_text().strip()
    except OSError:
        return "failed"
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "failed"
    status = data.get("status") if isinstance(data, dict) else None
    return status if status in ("done", "failed") else "failed"


def _wait_pid(pid: int) -> None:
    try:
        psutil.Process(pid).wait()
    except psutil.NoSuchProcess:
        pass
    except psutil.Error as exc:  # AccessDenied etc. — best effort
        logger.warning("wait on adopted pid %s failed: %r", pid, exc)


def _identity_ok(pid: int, pid_start_time) -> bool:
    if pid_start_time is None or not psutil.pid_exists(pid):
        return False
    try:
        return abs(psutil.Process(pid).create_time() - pid_start_time) < _IDENTITY_TOLERANCE_S
    except psutil.Error:
        return False


async def _adopt(db, session_id: str, pid: int) -> None:
    await asyncio.to_thread(_wait_pid, pid)
    row = db.read(
        lambda c: c.execute(
            "SELECT result_path FROM worker_sessions WHERE id = ?", (session_id,)
        ).fetchone()
    )
    status = _resolve_file(Path(row["result_path"])) or "failed"
    await db.write(lambda c: store.session_exited(c, session_id, status))


async def reattach(db, run_dirs, registry) -> tuple[ReattachSummary, dict[str, asyncio.Task]]:
    rows = db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE status IN ('pending', 'running')"
        ).fetchall()
    )
    summary = ReattachSummary(scanned=len(rows))
    adopted_tasks: dict[str, asyncio.Task] = {}

    for r in rows:
        sid = r["id"]
        if r["status"] == "pending":
            await db.write(lambda c, sid=sid: store.session_unknown(c, sid))
            await db.write(
                lambda c, r=r: store.mark_needs_human(
                    c, r["work_item_id"], r["node_id"],
                    "reattach: session pending, spawn unconfirmed",
                )
            )
            summary.unknown.append(sid)
            continue

        pid = r["pid"]
        if pid is not None and _identity_ok(pid, r["pid_start_time"]):
            await db.write(lambda c, sid=sid: store.session_reattached(c, sid))
            adopted_tasks[sid] = asyncio.create_task(_adopt(db, sid, pid))
            summary.adopted.append(sid)
            continue

        status = _resolve_file(Path(r["result_path"]))
        if status is not None:
            await db.write(lambda c, sid=sid: store.session_reattached(c, sid))
            await db.write(lambda c, sid=sid, status=status: store.session_exited(c, sid, status))
            summary.resolved_from_file.append(sid)
        else:
            await db.write(lambda c, sid=sid: store.session_unknown(c, sid))
            await db.write(
                lambda c, r=r: store.mark_needs_human(
                    c, r["work_item_id"], r["node_id"],
                    "reattach: running session, PID identity unconfirmed, no result",
                )
            )
            summary.unknown.append(sid)

    active = db.read(
        lambda c: c.execute("SELECT id FROM work_items WHERE status = 'active'").fetchall()
    )
    summary.resumed_work_items = [row["id"] for row in active]
    return summary, adopted_tasks
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_reattach.py -q`
Expected: PASS (4 tests)

- [ ] **Step 5: Full suite + lint + commit**

```bash
uv run pytest -q
uv run ruff check src/kraft/reattach.py tests/test_reattach.py
git add src/kraft/reattach.py tests/test_reattach.py
git commit -m "feat(reattach): startup session scan with psutil identity check"
```

---

## Task 5: `fixtures/fake-claude.sh`

**Files:**
- Create: `fixtures/fake-claude.sh` (executable)
- Test: covered by Task 7 (`test_api_reattach.py`); add one direct smoke test here.
- Test: `tests/test_fixtures.py` (create)

**Interfaces:**
- Produces: an executable script invoked as `fake-claude.sh -p <instr> --append-system-prompt <ctx> --output-format json`, CWD = the worktree. Reads env `KRAFT_FAKE_CLAUDE` (`fix` default | `noop` | `slow`), `KRAFT_FAKE_CLAUDE_DELAY` (seconds, default `10`), `KRAFT_RESULT_PATH`. Always writes `{"status":"done"}` to `$KRAFT_RESULT_PATH`, prints `{"type":"result","is_error":false}` as the last stdout line, exits 0.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fixtures.py`:

```python
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "fixtures" / "fake-claude.sh"


def _run(cwd, env_extra):
    env = {**os.environ, "KRAFT_RESULT_PATH": str(cwd / "result.json"), **env_extra}
    return subprocess.run(
        [str(_SCRIPT), "-p", "fix it", "--append-system-prompt", "ctx", "--output-format", "json"],
        cwd=cwd, env=env, capture_output=True, text=True,
    )


def test_fix_mode_patches_calc_and_writes_result(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    proc = _run(tmp_path, {"KRAFT_FAKE_CLAUDE": "fix"})
    assert proc.returncode == 0
    assert "a + b" in (tmp_path / "calc.py").read_text()
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}
    assert json.loads(proc.stdout.strip().splitlines()[-1])["is_error"] is False


def test_noop_mode_leaves_calc_but_writes_result(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    proc = _run(tmp_path, {"KRAFT_FAKE_CLAUDE": "noop"})
    assert proc.returncode == 0
    assert "a - b" in (tmp_path / "calc.py").read_text()
    assert json.loads((tmp_path / "result.json").read_text()) == {"status": "done"}


def test_slow_mode_delays(tmp_path):
    import time

    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    start = time.monotonic()
    proc = _run(tmp_path, {"KRAFT_FAKE_CLAUDE": "slow", "KRAFT_FAKE_CLAUDE_DELAY": "1"})
    assert proc.returncode == 0
    assert time.monotonic() - start >= 1.0
    assert "a + b" in (tmp_path / "calc.py").read_text()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_fixtures.py -q`
Expected: FAIL — script not found / not executable

- [ ] **Step 3: Implement `fixtures/fake-claude.sh`**

```bash
#!/usr/bin/env bash
# Stand-in for `claude` headless. Invoked as:
#   fake-claude.sh -p <instr> --append-system-prompt <ctx> --output-format json
# CWD is the worktree. Modes via KRAFT_FAKE_CLAUDE: fix (default) | noop | slow.
# Always writes $KRAFT_RESULT_PATH so an adopted session can resolve after a restart.
set -eu

mode="${KRAFT_FAKE_CLAUDE:-fix}"

if [ "$mode" = "slow" ]; then
  sleep "${KRAFT_FAKE_CLAUDE_DELAY:-10}"
  mode="fix"
fi

if [ "$mode" = "fix" ] && [ -f calc.py ]; then
  # portable in-place edit: rewrite the file
  tmp="$(mktemp)"
  sed 's/a - b/a + b/' calc.py > "$tmp" && mv "$tmp" calc.py
fi

if [ -n "${KRAFT_RESULT_PATH:-}" ]; then
  printf '{"status": "done"}' > "$KRAFT_RESULT_PATH"
fi

printf '{"type": "result", "is_error": false}\n'
```

Then: `chmod +x fixtures/fake-claude.sh`

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_fixtures.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Lint + commit**

```bash
uv run ruff check tests/test_fixtures.py
git add fixtures/fake-claude.sh tests/test_fixtures.py
git update-index --chmod=+x fixtures/fake-claude.sh
git commit -m "feat(fixtures): fake-claude.sh with fix/noop/slow modes"
```

---

## Task 6: `kraft.api` + hermetic API tests

**Files:**
- Modify: `pyproject.toml` (deps)
- Create: `src/kraft/api.py`
- Modify: `tests/support/harness.py` (add `fake_templates_dir`)
- Test: `tests/test_api.py` (create)

**Interfaces:**
- Consumes: `Database.open`, `load_registry`, `load_templates`, `reattach.reattach`, `executor.intake`, `executor.run`, `executor.resume`, `events.read_after`.
- Produces:
  - `kraft.api.app` — a module-level `FastAPI` instance with the lifespan wired.
  - `kraft.api.TEMPLATES_DIR: Path`, `kraft.api._bd_cwd() -> str | None` (reads `KRAFT_BD_CWD`).
  - Endpoints exactly per spec §4.2.
  - `app.state` after startup carries: `db`, `run_dirs`, `registry`, `templates` (a `TemplateSet`), `reattach_summary` (`ReattachSummary`), `tasks` (`dict[str, asyncio.Task]`).
  - `harness.fake_templates_dir(tmp_path: Path, agent_command: str) -> Path` — writes a `templates/` dir containing `quick-task.yaml` (copied) and a `registry.yaml` whose `on.implementation.start` binds `{kind: agent, command: agent_command}` and `on.test.run` binds `{kind: subprocess, command: ["python", "-m", "pytest", "-q"]}`.

- [ ] **Step 1: Add dependencies**

```bash
uv add fastapi uvicorn
uv add --dev httpx
```

Verify `pyproject.toml` `dependencies` now lists `fastapi` and `uvicorn`, and `[dependency-groups].dev` lists `httpx`.

- [ ] **Step 2: Add the `fake_templates_dir` helper**

Append to `tests/support/harness.py` (it already imports `Path`, `shutil`; add `import yaml` at top):

```python
def fake_templates_dir(tmp_path: Path, agent_command: str) -> Path:
    d = tmp_path / "templates"
    d.mkdir(parents=True, exist_ok=True)
    shutil.copy(_REPO_ROOT / "templates" / "quick-task.yaml", d / "quick-task.yaml")
    (d / "registry.yaml").write_text(
        yaml.safe_dump(
            {
                "hooks": {
                    "on.env.prepare": {"kind": "builtin", "handler": "env_setup"},
                    "on.implementation.start": {"kind": "agent", "command": agent_command},
                    "on.test.run": {"kind": "subprocess", "command": ["python", "-m", "pytest", "-q"]},
                }
            }
        )
    )
    return d
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_api.py`:

```python
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch, *, templates_dir=None):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv(
        "KRAFT_TEMPLATES_DIR",
        str(templates_dir or fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))),
    )
    import kraft.api as api

    return TestClient(api.app)


def _poll_events(client, wid, want, timeout=30):
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        seen = client.get(f"/work-items/{wid}/events").json()
        if any(e["type"] == want for e in seen):
            return seen
        time.sleep(0.2)
    raise AssertionError(f"{want} not seen; got {[e['type'] for e in seen]}")


def test_health_ok_and_degraded(tmp_path, monkeypatch):
    # a templates dir with one bad-hook template
    bad = fake_templates_dir(tmp_path, "claude")
    (bad / "broken.yaml").write_text(
        "id: broken\nnodes:\n  - {id: x, tasks: [on.nope], gate_after: null}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=bad) as client:
        body = client.get("/health").json()
        assert body["status"] == "degraded"
        assert "broken" in body["invalid_templates"]
        assert "reattach_summary" in body


def test_post_materializes_chain(tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        r = client.post("/work-items", json={"title": "make the failing test pass", "repo": str(repo)})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["current_node_id"] == "env_setup"
        assert [n["id"] for n in body["chain_definition"]["nodes"]] == [
            "env_setup", "implementation", "verify",
        ]
        assert body["bead_id"]


def test_post_invalid_template_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post("/work-items", json={"title": "x", "repo": "/tmp", "chain_template": "nope"})
        assert r.status_code == 422


def test_post_missing_repo_422(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        r = client.post("/work-items", json={"title": "x"})
        assert r.status_code == 422


def test_happy_path_via_api(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items", json={"title": "make the failing test pass", "repo": str(repo)}
        ).json()["id"]
        _poll_events(client, wid, "work_item_completed")

        item = client.get(f"/work-items/{wid}").json()
        assert item["status"] == "completed"
        assert len(item["worker_sessions"]) == 3

        run_dir = Path(client.app.state.run_dirs.base)
        assert "a + b" in (run_dir / "worktrees" / wid / "calc.py").read_text()

        # log endpoint returns the agent's stdout
        impl = next(s for s in item["worker_sessions"] if s["node_id"] == "implementation")
        log = client.get(f"/worker-sessions/{impl['id']}/log")
        assert log.status_code == 200
        assert "is_error" in log.text


def test_get_unknown_work_item_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/work-items/does-not-exist").status_code == 404
        assert client.get("/worker-sessions/nope/log").status_code == 404
```

- [ ] **Step 4: Run to verify they fail**

Run: `uv run pytest tests/test_api.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.api'`

- [ ] **Step 5: Implement `src/kraft/api.py`**

```python
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from kraft import events, executor, reattach
from kraft.db import Database
from kraft.paths import RunDirs
from kraft.templates import load_registry, load_templates

TEMPLATES_DIR = Path(
    os.environ.get("KRAFT_TEMPLATES_DIR")
    or Path(__file__).resolve().parents[2] / "templates"
)


def _bd_cwd() -> str | None:
    return os.environ.get("KRAFT_BD_CWD") or None


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.tasks = {}
    run_dirs = RunDirs(Path(os.environ.get("KRAFT_RUN_DIR", ".kraft-run"))).ensure()
    database = await Database.open(run_dirs.db)
    registry = load_registry(TEMPLATES_DIR / "registry.yaml")
    templates = load_templates(TEMPLATES_DIR, registry)
    summary, adopted = await reattach.reattach(database, run_dirs, registry)
    for wid in summary.resumed_work_items:
        _spawn(app, wid, executor.resume(
            database, run_dirs, work_item_id=wid, registry=registry,
            adopted=adopted, bd_cwd=_bd_cwd(),
        ))

    app.state.db = database
    app.state.run_dirs = run_dirs
    app.state.registry = registry
    app.state.templates = templates
    app.state.reattach_summary = summary
    try:
        yield
    finally:
        for task in list(app.state.tasks.values()):
            task.cancel()
        await database.close()


def _spawn(app: FastAPI, wid: str, coro) -> asyncio.Task:
    task = asyncio.ensure_future(coro)
    app.state.tasks[wid] = task
    task.add_done_callback(lambda _t, wid=wid: app.state.tasks.pop(wid, None))
    return task


app = FastAPI(lifespan=lifespan)


class NewWorkItem(BaseModel):
    title: str
    repo: str
    chain_template: str = "quick-task"


@app.post("/work-items", status_code=201)
async def create_work_item(body: NewWorkItem, request: Request):
    st = request.app.state
    template = st.templates.valid.get(body.chain_template)
    if template is None:
        raise HTTPException(422, "unknown or invalid template")
    try:
        wid = await executor.intake(
            st.db, st.run_dirs, title=body.title, repo=body.repo,
            template=template, bd_cwd=_bd_cwd(),
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        raise HTTPException(502, f"bd intake failed: {exc}") from exc

    _spawn(
        request.app, wid,
        executor.run(st.db, st.run_dirs, work_item_id=wid, registry=st.registry, bd_cwd=_bd_cwd()),
    )
    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    return JSONResponse(
        status_code=201,
        content={
            "id": wid,
            "bead_id": row["bead_id"],
            "status": row["status"],
            "chain_definition": json.loads(row["chain_definition"]),
            "current_node_id": row["current_node_id"],
        },
    )


def _work_item_row(st, wid):
    row = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown work item")
    return row


@app.get("/work-items/{wid}")
async def get_work_item(wid: str, request: Request):
    st = request.app.state
    row = _work_item_row(st, wid)
    sessions = st.db.read(
        lambda c: c.execute(
            "SELECT * FROM worker_sessions WHERE work_item_id = ? ORDER BY created_at", (wid,)
        ).fetchall()
    )
    return {
        **{k: row[k] for k in row.keys()},
        "chain_definition": json.loads(row["chain_definition"]),
        "worker_sessions": [{k: s[k] for k in s.keys()} for s in sessions],
    }


@app.get("/work-items/{wid}/events")
async def get_events(wid: str, request: Request, after_seq: int = 0):
    st = request.app.state
    _work_item_row(st, wid)
    return st.db.read(lambda c: events.read_after(c, after_seq, wid))


@app.get("/worker-sessions/{sid}/log")
async def get_log(sid: str, request: Request):
    st = request.app.state
    row = st.db.read(
        lambda c: c.execute(
            "SELECT log_path FROM worker_sessions WHERE id = ?", (sid,)
        ).fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown session")
    path = Path(row["log_path"])
    if not path.exists():
        raise HTTPException(404, "log not found")
    return PlainTextResponse(path.read_text())


@app.get("/health")
async def health(request: Request):
    st = request.app.state
    invalid = st.templates.invalid
    return {
        "status": "degraded" if invalid else "ok",
        "invalid_templates": invalid,
        "reattach_summary": asdict(st.reattach_summary),
    }
```

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_api.py -q`
Expected: PASS (7 tests). If `test_happy_path_via_api` flakes on timing, raise the poll timeout — do **not** add sleeps to the app.

- [ ] **Step 7: Full suite + lint + commit**

```bash
uv run pytest -q
uv run ruff check src/kraft/api.py tests/test_api.py tests/support/harness.py
git add pyproject.toml uv.lock src/kraft/api.py tests/test_api.py tests/support/harness.py
git commit -m "feat(api): FastAPI orchestrator surface with reattach lifespan"
```

---

## Task 7: `kraft.__main__` + subprocess-server reattach tests

**Files:**
- Create: `src/kraft/__main__.py`
- Create: `tests/support/server.py`
- Test: `tests/test_api_reattach.py` (create)

**Interfaces:**
- Consumes: `kraft.api:app`, `uvicorn`.
- Produces:
  - `python -m kraft` starts uvicorn on `127.0.0.1:${KRAFT_PORT:-8765}`, single worker, no reload.
  - `tests.support.server.running_server(*, run_dir: Path, templates_dir: Path, bd_cwd: Path, env: dict | None = None)` — context manager yielding a `Server` with `.base` (str URL), `.port` (int), `.client` (`httpx.Client` bound to `.base`), `.proc` (`subprocess.Popen`), `.kill()` (SIGKILL + wait). Waits up to 15s for `GET /health` → 200 before yielding. On exit, kills the process if still alive.

- [ ] **Step 1: Implement `src/kraft/__main__.py`**

```python
from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "kraft.api:app",
        host="127.0.0.1",
        port=int(os.environ.get("KRAFT_PORT", "8765")),
        log_level="warning",
    )
```

- [ ] **Step 2: Implement `tests/support/server.py`**

```python
from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Server:
    proc: subprocess.Popen
    base: str
    port: int
    client: httpx.Client

    def kill(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


@contextlib.contextmanager
def running_server(*, run_dir: Path, templates_dir: Path, bd_cwd: Path, env: dict | None = None):
    port = _free_port()
    child_env = {
        **os.environ,
        "KRAFT_RUN_DIR": str(run_dir),
        "KRAFT_TEMPLATES_DIR": str(templates_dir),
        "KRAFT_BD_CWD": str(bd_cwd),
        "KRAFT_PORT": str(port),
        **(env or {}),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "kraft"], cwd=_REPO_ROOT, env=child_env
    )
    base = f"http://127.0.0.1:{port}"
    client = httpx.Client(base_url=base, timeout=10.0)
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early rc={proc.returncode}")
            with contextlib.suppress(httpx.TransportError):
                if client.get("/health").status_code == 200:
                    break
            time.sleep(0.2)
        else:
            raise RuntimeError("server did not become healthy in 15s")
        yield Server(proc, base, port, client)
    finally:
        client.close()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
```

- [ ] **Step 3: Write the failing tests**

Create `tests/test_api_reattach.py`:

```python
from __future__ import annotations

import time
from pathlib import Path

import psutil
from support.harness import fake_templates_dir, isolated_bd, make_repo
from support.server import running_server

from kraft import db as kdb
from kraft import store

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _poll(client, wid, want, timeout=30):
    deadline = time.monotonic() + timeout
    types = []
    while time.monotonic() < deadline:
        evs = client.get(f"/work-items/{wid}/events").json()
        types = [e["type"] for e in evs]
        if want in types:
            return evs
        time.sleep(0.2)
    raise AssertionError(f"{want} not seen; got {types}")


def test_reattach_adopts_running_agent(tmp_path):
    run_dir = tmp_path / "run"
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    slow_env = {"KRAFT_FAKE_CLAUDE": "slow", "KRAFT_FAKE_CLAUDE_DELAY": "20"}

    with running_server(run_dir=run_dir, templates_dir=templates, bd_cwd=tracker, env=slow_env) as srv:
        wid = srv.client.post(
            "/work-items", json={"title": "make the failing test pass", "repo": str(repo)}
        ).json()["id"]
        started = _poll(srv.client, wid, "worker_session_started")
        pid = next(e["payload"]["pid"] for e in started if e["type"] == "worker_session_started")
        assert psutil.pid_exists(pid)
        srv.kill()

    # the detached fake-claude.sh is still sleeping
    assert psutil.pid_exists(pid)

    with running_server(run_dir=run_dir, templates_dir=templates, bd_cwd=tracker, env=slow_env) as srv:
        health = srv.client.get("/health").json()
        assert wid in health["reattach_summary"]["resumed_work_items"]
        evs = _poll(srv.client, wid, "session_reattached", timeout=40)
        reattached_pid = next(
            e["payload"]["pid"] for e in evs if e["type"] == "session_reattached"
        )
        assert reattached_pid == pid  # not re-spawned
        _poll(srv.client, wid, "work_item_completed", timeout=60)
        assert "a + b" in (run_dir / "worktrees" / wid / "calc.py").read_text()


def test_reattach_pending_session_is_unknown(tmp_path):
    """Seed a pending session + active work item, then start the server."""
    run_dir = tmp_path / "run"
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)

    import asyncio
    import json

    chain = json.dumps({
        "template_id": "quick-task",
        "nodes": [
            {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
            {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
            {"id": "verify", "tasks": ["on.test.run"], "gate_after": None},
        ],
    })

    async def seed():
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
        (run_dir / "results").mkdir(parents=True, exist_ok=True)
        database = await kdb.Database.open(run_dir / "orchestrator.db")
        try:
            await database.write(lambda c: store.create_work_item(
                c, id="w-seed", bead_id="B-1", title="t", repo="/r",
                chain_template="quick-task", chain_definition=chain,
            ))
            await database.write(lambda c: store.load_chain(c, "w-seed", "implementation"))
            await database.write(lambda c: store.enter_node(c, "w-seed", "implementation"))
            await database.write(lambda c: store.create_session(
                c, id="s-seed", work_item_id="w-seed", node_id="implementation",
                hook_point="on.implementation.start",
                log_path=str(run_dir / "logs" / "s-seed.log"),
                result_path=str(run_dir / "results" / "s-seed.json"),
            ))
        finally:
            await database.close()

    asyncio.run(seed())

    with running_server(run_dir=run_dir, templates_dir=templates, bd_cwd=tracker) as srv:
        health = srv.client.get("/health").json()
        assert "s-seed" in health["reattach_summary"]["unknown"]
        assert srv.client.get("/work-items/w-seed").json()["status"] == "needs_human"
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_api_reattach.py -q`
Expected: PASS (2 tests). These are slower (real subprocess + sleeps) — allow ~90s.

- [ ] **Step 5: Full suite + lint + commit**

```bash
uv run pytest -q
uv run ruff check src/kraft/__main__.py tests/support/server.py tests/test_api_reattach.py
git add src/kraft/__main__.py tests/support/server.py tests/test_api_reattach.py
git commit -m "feat: python -m kraft entrypoint + subprocess reattach tests"
```

---

## Task 8: Gated e2e happy-path test

**Files:**
- Create: `tests/conftest.py` (root — `e2e` skip logic)
- Modify: `tests/support/harness.py` (add `e2e_templates_dir`)
- Test: `tests/test_e2e.py` (create)

**Interfaces:**
- Consumes: `running_server`, `make_repo`, `isolated_bd`.
- Produces:
  - Root `tests/conftest.py` — a `pytest_collection_modifyitems` hook that skips any item marked `e2e` unless `os.environ.get("KRAFT_E2E") == "1"` **and** `shutil.which("claude")` is truthy.
  - `harness.e2e_templates_dir(tmp_path: Path) -> Path` — like `fake_templates_dir` but binds `on.implementation.start` to `{kind: agent, command: "claude --model claude-haiku-4-5-20251001"}` (the agent adapter `shlex.split`s the command, so the flag rides along).

- [ ] **Step 1: Implement the skip hook**

Create `tests/conftest.py`:

```python
from __future__ import annotations

import os
import shutil

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("KRAFT_E2E") == "1" and shutil.which("claude"):
        return
    skip = pytest.mark.skip(reason="e2e: set KRAFT_E2E=1 and install `claude` to run")
    for item in items:
        if "e2e" in item.keywords:
            item.add_marker(skip)
```

- [ ] **Step 2: Add `e2e_templates_dir`**

Append to `tests/support/harness.py`:

```python
def e2e_templates_dir(tmp_path: Path) -> Path:
    return fake_templates_dir(tmp_path, "claude --model claude-haiku-4-5-20251001")
```

- [ ] **Step 3: Write the test**

Create `tests/test_e2e.py`:

```python
from __future__ import annotations

import time
from pathlib import Path

import pytest
from support.harness import e2e_templates_dir, isolated_bd, make_repo
from support.server import running_server

pytestmark = pytest.mark.e2e


def _poll(client, wid, want, timeout=180):
    deadline = time.monotonic() + timeout
    types = []
    while time.monotonic() < deadline:
        evs = client.get(f"/work-items/{wid}/events").json()
        types = [e["type"] for e in evs]
        if want in types:
            return evs
        if "work_item_needs_human" in types:
            raise AssertionError(f"chain went to needs_human; events={types}")
        time.sleep(1.0)
    raise AssertionError(f"{want} not seen in {timeout}s; got {types}")


def test_e2e_happy_path(tmp_path):
    run_dir = tmp_path / "run"
    templates = e2e_templates_dir(tmp_path)
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    claude_md = repo / "CLAUDE.md"
    claude_md_before = claude_md.read_text() if claude_md.exists() else None

    with running_server(run_dir=run_dir, templates_dir=templates, bd_cwd=tracker) as srv:
        wid = srv.client.post(
            "/work-items", json={"title": "make the failing test pass", "repo": str(repo)}
        ).json()["id"]
        _poll(srv.client, wid, "work_item_completed")

        item = srv.client.get(f"/work-items/{wid}").json()
        assert item["status"] == "completed"
        assert "a + b" in (run_dir / "worktrees" / wid / "calc.py").read_text()

        # context-injection boundary: the agent never wrote into the target repo
        after = claude_md.read_text() if claude_md.exists() else None
        assert after == claude_md_before

    # bead closed in the isolated tracker
    import subprocess

    show = subprocess.run(
        ["bd", "show", item["bead_id"], "--json"], cwd=tracker,
        capture_output=True, text=True, check=True,
    )
    import json as _json

    assert _json.loads(show.stdout)[0]["status"] == "closed"
```

- [ ] **Step 4: Verify the test is skipped by default**

Run: `uv run pytest tests/test_e2e.py -q`
Expected: `1 skipped`

- [ ] **Step 5: Verify it runs when enabled (only if `claude` is installed locally)**

Run: `KRAFT_E2E=1 uv run pytest tests/test_e2e.py -q`
Expected: PASS (or `skipped` if `claude` not on PATH). If `claude` is unavailable, note it in the handoff — do not block the task.

- [ ] **Step 6: Lint + commit**

```bash
uv run ruff check tests/conftest.py tests/test_e2e.py tests/support/harness.py
git add tests/conftest.py tests/test_e2e.py tests/support/harness.py
git commit -m "test(e2e): gated real-claude happy path"
```

---

## Task 9: Wire-up verification + docs + bead close

**Files:**
- Modify: `docs/superpowers/specs/2026-09-01-skeleton-c-resilience-api.md` (flip status, tick §8)
- Modify: `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md` (mark Chunk C done)

**Interfaces:** none — verification only.

- [ ] **Step 1: Full hermetic gate**

Run: `uv run pytest -q`
Expected: PASS, `0 failed`, e2e `skipped`. Record the count.

- [ ] **Step 2: Lint the whole tree**

Run: `uv run ruff check`
Expected: clean.

- [ ] **Step 3: Manual smoke (optional but recommended)**

```bash
KRAFT_RUN_DIR=/tmp/kraft-smoke uv run python -m kraft &
sleep 2
curl -s localhost:8765/health | python -m json.tool
kill %1
rm -rf /tmp/kraft-smoke
```
Expected: `{"status": "ok", "invalid_templates": {}, "reattach_summary": {...}}`.

- [ ] **Step 4: Update the spec**

In `docs/superpowers/specs/2026-09-01-skeleton-c-resilience-api.md`: change `**Status:** design agreed, not yet built` → `**Status:** implemented <today>`, and tick the boxes in §8 that the tests now cover.

- [ ] **Step 5: Update the master plan**

In `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md`, §1 table: mark Chunk C done (e.g. `**C — Resilience + API** ✅`). Add a line under §3 noting the skeleton is green and the deferred master plans can begin.

- [ ] **Step 6: Close the bead**

```bash
bd close Kraft-rnx.3 --reason="Chunk C implemented: reattach, FastAPI, resume, e2e. Hermetic gate green."
bd show Kraft-rnx --json   # if all children closed, close the parent too
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/specs/2026-09-01-skeleton-c-resilience-api.md docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md
git commit -m "docs: mark skeleton Chunk C complete"
```

- [ ] **Step 8: Handoff**

Report to the user: test count, any skipped e2e (and why), `git log --oneline` for the chunk, and the proposed merge command (`git checkout main && git merge --no-ff <branch>` — do **not** run it without approval).

---

## Self-Review

**1. Spec coverage:**

| Spec section | Task |
|---|---|
| §1 handoff (a) recovery semantics | Task 4 (`reattach` state table) |
| §1 handoff (b) `run` not resumable | Task 2 + Task 3 (`_walk_node`, `resume`) |
| §1 handoff (c) no Registry on reattach | Task 6 (lifespan reloads `registry.yaml`) |
| §2 `kraft.reattach` — pending→unknown | Task 4 `test_pending_session_becomes_unknown_and_needs_human` |
| §2 adopt live pid | Task 4 `test_live_pid_matching_identity_is_adopted` |
| §2 resolve-from-file | Task 4 `test_running_dead_pid_resolves_from_result_file` |
| §2 conservative fallback → unknown | Task 4 `test_running_dead_pid_no_file_is_unknown` |
| §2.3 `ReattachSummary` | Task 4 (dataclass) |
| §3 `executor.resume` | Task 3 (3 tests) |
| §3.1 `_walk_node` extraction | Task 2 |
| §4.1 lifespan (reattach before serving, task registry) | Task 6 |
| §4.2 `POST /work-items` (201, 422 invalid template, 422 missing repo, 502 bd fail) | Task 6 tests |
| §4.2 `GET /work-items/{id}` (+ sessions, 404) | Task 6 `test_happy_path_via_api`, `test_get_unknown_work_item_404` |
| §4.2 `GET .../events?after_seq=` | Task 6 (`_poll_events` uses it) |
| §4.2 `GET /worker-sessions/{id}/log` (plain text, 404) | Task 6 |
| §4.2 `GET /health` (ok/degraded, invalid_templates, reattach_summary) | Task 6 `test_health_ok_and_degraded` |
| §4.3 `kraft.__main__` | Task 7 |
| §5.1 `fake-claude.sh` 3 modes + always writes result | Task 5 |
| §5.2 `tests/support/server.py` | Task 7 |
| §5.3 hermetic tests (all rows) | Tasks 4–7 |
| §5.3 `test_reattach_running_session` (SIGKILL, same pid, completes) | Task 7 `test_reattach_adopts_running_agent` |
| §5.3 `test_reattach_pending_unknown` | Task 7 `test_reattach_pending_session_is_unknown` |
| §5.3 `test_reattach_dead_pid_from_file` | Task 4 `test_running_dead_pid_resolves_from_result_file` |
| §5.3 e2e `test_e2e_happy_path` (gated, CLAUDE.md untouched) | Task 8 |
| §6 store `unknown` status + `session_unknown` event | Task 1 |
| §6 `_walk_node` extraction | Task 2 |
| §6 `events.read_after` work-item filter | already exists (`events.py:20-40`) — no task needed |
| §6 `pyproject.toml` deps | Task 6 Step 1 |
| §7 build order | Tasks ordered A/B-tweak → resume → reattach → fixtures → api → main → e2e |
| §8 done-when checklist | Task 9 |
| §10 adopted↔resume coordination | Task 3 `_reconcile_current_node` awaits `adopted[sid]`; Task 4 `_adopt` writes `session_exited` before its task completes |

No gaps.

**2. Placeholder scan:** No `TBD`/`TODO`/"handle edge cases"/"similar to Task N". Every code step has real code. The one `# ponytail:` comment (multi-task mid-fan-out resume) is a deliberate, named ceiling per house style, not a placeholder.

**3. Type consistency:**
- `reattach.reattach() -> tuple[ReattachSummary, dict[str, asyncio.Task]]` — dict keyed by session id; consumed in Task 6 lifespan as `adopted` passed straight to `executor.resume(..., adopted=...)`; consumed in Task 3 as `adopted.get(s["id"])`. Consistent.
- `ReattachSummary` fields (`scanned`, `adopted`, `resolved_from_file`, `unknown`, `resumed_work_items`) — asserted by name in Task 4 tests, `asdict()`'d in Task 6 `/health`, keyed by string in Task 7 tests (`["resumed_work_items"]`, `["unknown"]`). Consistent.
- `executor._walk_node(...) -> 'ok' | 'needs_human'` — Task 2 defines, Task 3 `_reconcile_current_node` and `resume` consume with `== "ok"` / `== "needs_human"`. Consistent.
- `executor.resume(db, run_dirs, *, work_item_id, registry, adopted, bd_cwd=None)` — Task 3 defines, Task 6 lifespan calls with exactly these kwargs. Consistent.
- `store.session_unknown(conn, session_id)` / `store.session_reattached(conn, session_id)` — Task 1 defines, Task 4 consumes via `lambda c: store.session_unknown(c, sid)`. Consistent.
- `harness.fake_templates_dir(tmp_path, agent_command)` — Task 6 defines, Tasks 6/7/8 consume; `e2e_templates_dir(tmp_path)` wraps it (Task 8). Consistent.
- `server.running_server(*, run_dir, templates_dir, bd_cwd, env=None)` → `Server(proc, base, port, client)` with `.kill()` — Task 7 defines, Tasks 7/8 consume `srv.client`, `srv.kill()`, `srv.base`. Consistent.
- API `app.state` attributes (`db`, `run_dirs`, `registry`, `templates`, `reattach_summary`, `tasks`) — set in Task 6 lifespan, read in Task 6 endpoints + `test_happy_path_via_api` (`client.app.state.run_dirs.base`). Consistent.

No inconsistencies found.

---

## Notes for the executor

- **Do not** add `pytest-asyncio`. Follow the existing pattern: a sync `test_*` that defines an inner `async def scenario()` and ends with `asyncio.run(scenario())`. API tests use `TestClient` as a context manager (it runs the lifespan).
- **`TestClient` runs the app loop in a worker thread.** The executor `asyncio.Task`s run there; a `client.get(...)` call pumps that loop, so polling `/events` between short `time.sleep`s lets the chain progress. If a test hangs, it's almost always a real deadlock in the app, not the test — investigate, don't paper over with longer sleeps.
- **`db._connect` uses `check_same_thread=True`.** Everything (writer task, reads) runs on one thread per process — keep it that way. No DB access from `asyncio.to_thread` callbacks.
- **`_adopt` ordering (Task 4):** it must `await db.write(store.session_exited(...))` *before* the task object completes, so `_reconcile_current_node`'s re-read (Task 3) sees the final status. The code already does this — don't reorder.
- **Subprocess-server tests are slow and can flake under load.** If `test_api_reattach.py` is flaky in CI, mark it `@pytest.mark.slow` and exclude from the fast gate — but keep it in the default `uv run pytest -q` run for now.
- Commit after every task. Do not push, do not merge to `main`.
