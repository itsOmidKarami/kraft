# Effort 3A — WebSocket Transport Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a live event-push transport (`WS /ws/events`) plus the two REST endpoints (`GET /work-items`, `GET /templates`) and static-SPA serving the React UI (Effort 3B) will consume.

**Architecture:** A post-commit hook on `Database` pokes an in-process `Broadcaster`. The broadcaster's single fan-out task reads the new rows from the `events` table (`events.read_after(cursor)`) and pushes them onto one bounded `asyncio.Queue` per connected WebSocket. Each `WS /ws/events` connection replays `after_seq` history, then streams its queue. The `events` table is the only queue of record — nothing is broadcast that did not commit.

**Tech Stack:** Python 3.14, asyncio, FastAPI/Starlette WebSockets, SQLite (WAL), pytest + `fastapi.testclient.TestClient` (has `websocket_connect`).

**Spec:** `docs/superpowers/specs/2026-09-03-effort-3-ws-transport-react-ui-design.md` (§2, §4, §5, §6.1, §6.2, §8; chunk 3A of §7)

## Global Constraints

- Python `>=3.14` (`pyproject.toml`).
- No new runtime dependencies — FastAPI/Starlette (already present) ships WebSocket support.
- `from __future__ import annotations` at the top of every module (repo-wide).
- Ruff must pass: `uv run ruff check .` and `uv run ruff format --check .` (line-length 100, rules `E,F,W,I,UP,B`).
- No schema change. No edits to `kraft.events`, `kraft.store`, `kraft.executor`, `kraft.reattach`.
- Config via env var mirrors the existing pattern: read in `lifespan`, not at import (`KRAFT_TEMPLATES_DIR`, `KRAFT_RUN_DIR` precedent).
- Tests are hermetic (no real `claude`); use `tests/support/harness.py` helpers.
- Every non-trivial task ends with a commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kraft/db.py` (modify) | `Database` gains an optional post-commit callback + `set_on_commit`. |
| `src/kraft/ws.py` (create) | `Broadcaster` — per-client queues, cursor, `notify()`, fan-out task. No FastAPI imports; testable against a bare `Database`. |
| `src/kraft/api.py` (modify) | Wire `Broadcaster` into `lifespan`; add `WS /ws/events`, `GET /work-items`, `GET /templates`; resolve the frontend dist dir; add the SPA catch-all route. |
| `tests/test_ws.py` (create) | `on_commit` firing, `Broadcaster` fan-out + overflow, `WS /ws/events` catch-up/live/reconnect/origin. |
| `tests/test_api.py` (modify) | `GET /work-items` shape + cursor; `GET /templates`; SPA catch-all present/absent. |

---

## Task 1: `Database` post-commit hook

**Files:**
- Modify: `src/kraft/db.py` (`Database.__init__`, `Database.open`, `Database._run`; add `set_on_commit`)
- Test: `tests/test_ws.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `Database.__init__(self, writer, reader, *, on_commit: Callable[[], None] | None = None)`
  - `Database.open(cls, path, *, on_commit: Callable[[], None] | None = None) -> Database`
  - `Database.set_on_commit(self, cb: Callable[[], None] | None) -> None`
  - Behavior: after each **successful** committed write, `_run` calls `on_commit()` (if set) *after* resolving the caller's future. A raising `on_commit` is logged (`logger.exception`) and swallowed. Not called on a failed write.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ws.py`:

```python
from __future__ import annotations

import asyncio

import pytest

from kraft import db, events, store


async def _seed(database, wid="w1"):
    await database.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
            "status, created_at, updated_at) VALUES (?, 't', '/r', 'quick-task', '{}', "
            "'active', 'now', 'now')",
            (wid,),
        )
    )


def test_on_commit_fires_after_successful_write_and_not_after_failure(tmp_path):
    async def scenario():
        calls = []
        database = await db.Database.open(
            tmp_path / "orchestrator.db", on_commit=lambda: calls.append(1)
        )
        try:
            await _seed(database)
            assert len(calls) == 1  # one commit so far

            def boom(c):
                c.execute("UPDATE work_items SET status='completed' WHERE id='w1'")
                raise RuntimeError("boom")

            with pytest.raises(RuntimeError):
                await database.write(boom)
            assert len(calls) == 1  # failed write did NOT fire on_commit
        finally:
            await database.close()

    asyncio.run(scenario())


def test_on_commit_exception_is_swallowed(tmp_path):
    async def scenario():
        def bad():
            raise ValueError("listener broke")

        database = await db.Database.open(tmp_path / "orchestrator.db", on_commit=bad)
        try:
            await _seed(database)  # must not raise despite the bad listener
            # writer still works
            row = database.read(
                lambda c: c.execute("SELECT id FROM work_items WHERE id='w1'").fetchone()
            )
            assert row["id"] == "w1"
        finally:
            await database.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_ws.py -q`
Expected: FAIL — `Database.open() got an unexpected keyword argument 'on_commit'`.

- [ ] **Step 3: Implement**

In `src/kraft/db.py`:

Add `import logging` and `logger = logging.getLogger(__name__)` near the top (after imports).

`__init__`:

```python
def __init__(
    self,
    writer: sqlite3.Connection,
    reader: sqlite3.Connection,
    *,
    on_commit: Callable[[], None] | None = None,
) -> None:
    self._writer = writer
    self._reader = reader
    self._on_commit = on_commit
    self._queue: asyncio.Queue = asyncio.Queue()
    self._task: asyncio.Task | None = None

def set_on_commit(self, cb: Callable[[], None] | None) -> None:
    self._on_commit = cb
```

`open`:

```python
@classmethod
async def open(
    cls, path: str | Path, *, on_commit: Callable[[], None] | None = None
) -> Database:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = _connect(path)
    migrate(writer)
    reader = _connect(path)
    self = cls(writer, reader, on_commit=on_commit)
    self._task = asyncio.create_task(self._run())
    return self
```

In `_run`, the success branch (`else:` after the `try`):

```python
else:
    if not fut.done():
        fut.set_result(result)
    if self._on_commit is not None:
        try:
            self._on_commit()
        except Exception:
            logger.exception("on_commit listener raised")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_ws.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Regression + lint**

Run: `uv run pytest tests/test_db.py tests/test_store.py tests/test_events.py -q && uv run ruff check src/kraft/db.py tests/test_ws.py`
Expected: all PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/db.py tests/test_ws.py
git commit -m "feat(db): post-commit on_commit hook for event broadcasting"
```

---

## Task 2: `Broadcaster`

**Files:**
- Create: `src/kraft/ws.py`
- Test: `tests/test_ws.py` (extend)

**Interfaces:**
- Consumes: `Database` (Task 1), `kraft.events.read_after`.
- Produces:
  - `class Client` — `.queue: asyncio.Queue`, `.dropped: bool`.
  - `class Broadcaster`:
    - `__init__(self, db: Database, *, maxsize: int = 1000)`
    - `async def start(self) -> None` — sets `_cursor` to current `MAX(seq)`, spawns the fan-out task.
    - `async def stop(self) -> None` — cancels + awaits the fan-out task.
    - `def notify(self) -> None` — `self._wakeup.set()` (sync; safe from `on_commit`).
    - `def register(self) -> Client`
    - `def unregister(self, client: Client) -> None`
    - `@property def cursor(self) -> int`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ws.py`:

```python
from kraft.ws import Broadcaster


def test_broadcaster_fans_committed_events_to_all_clients(tmp_path):
    async def scenario():
        bc_holder = {}
        database = await db.Database.open(
            tmp_path / "orchestrator.db", on_commit=lambda: bc_holder["bc"].notify()
        )
        bc = Broadcaster(database)
        bc_holder["bc"] = bc
        await bc.start()
        try:
            await _seed(database)
            c1, c2 = bc.register(), bc.register()
            await database.write(lambda c: events.append(c, "w1", "node_started", {"n": "a"}))
            await database.write(lambda c: events.append(c, "w1", "node_completed", {"n": "a"}))

            got1 = [await asyncio.wait_for(c1.queue.get(), 1) for _ in range(2)]
            got2 = [await asyncio.wait_for(c2.queue.get(), 1) for _ in range(2)]
            assert [e["type"] for e in got1] == ["node_started", "node_completed"]
            assert [e["type"] for e in got2] == ["node_started", "node_completed"]
            assert bc.cursor == got1[-1]["seq"]
        finally:
            await bc.stop()
            await database.close()

    asyncio.run(scenario())


def test_broadcaster_drops_a_client_whose_queue_overflows(tmp_path):
    async def scenario():
        bc_holder = {}
        database = await db.Database.open(
            tmp_path / "orchestrator.db", on_commit=lambda: bc_holder["bc"].notify()
        )
        bc = Broadcaster(database, maxsize=3)
        bc_holder["bc"] = bc
        await bc.start()
        try:
            await _seed(database)
            slow, ok = bc.register(), bc.register()
            for i in range(6):
                await database.write(
                    lambda c, i=i: events.append(c, "w1", "node_started", {"i": i})
                )
            await asyncio.sleep(0.05)  # let the fan-out task run
            assert slow.dropped is True
            # the healthy client still drained everything it could hold; drain it
            drained = []
            while not ok.queue.empty():
                drained.append(ok.queue.get_nowait())
            assert len(drained) >= 3
        finally:
            await bc.stop()
            await database.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_ws.py -q -k broadcaster`
Expected: FAIL — `No module named 'kraft.ws'`.

- [ ] **Step 3: Implement `src/kraft/ws.py`**

```python
from __future__ import annotations

import asyncio
import logging

from kraft import events
from kraft.db import Database

logger = logging.getLogger(__name__)


class Client:
    __slots__ = ("queue", "dropped")

    def __init__(self, maxsize: int) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self.dropped: bool = False


class Broadcaster:
    """Fans committed `events` rows to every connected WebSocket client.

    Fed by `Database.on_commit` -> `notify()`. The fan-out task is the only
    reader of the `events` tail; `_cursor` is the last seq it has delivered.
    A client whose bounded queue fills is marked `dropped` and starved — its
    WebSocket handler closes the socket and the browser reconnects, catching
    up from its own tracked `after_seq`.
    """

    def __init__(self, db: Database, *, maxsize: int = 1000) -> None:
        self._db = db
        self._maxsize = maxsize
        self._clients: set[Client] = set()
        self._cursor: int = 0
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def cursor(self) -> int:
        return self._cursor

    async def start(self) -> None:
        self._cursor = self._db.read(
            lambda c: c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        )
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def notify(self) -> None:
        self._wakeup.set()

    def register(self) -> Client:
        client = Client(self._maxsize)
        self._clients.add(client)
        return client

    def unregister(self, client: Client) -> None:
        self._clients.discard(client)

    async def _run(self) -> None:
        while True:
            await self._wakeup.wait()
            self._wakeup.clear()
            new = self._db.read(lambda c: events.read_after(c, self._cursor))
            for ev in new:
                for client in self._clients:
                    if client.dropped:
                        continue
                    try:
                        client.queue.put_nowait(ev)
                    except asyncio.QueueFull:
                        client.dropped = True
                        logger.warning("ws client queue overflow; dropping client")
                self._cursor = ev["seq"]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_ws.py -q -k broadcaster`
Expected: PASS (2 tests).

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/kraft/ws.py tests/test_ws.py && uv run ruff format src/kraft/ws.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/ws.py tests/test_ws.py
git commit -m "feat(ws): Broadcaster fan-out of committed events to per-client queues"
```

---

## Task 3: `WS /ws/events` endpoint + lifespan wiring

**Files:**
- Modify: `src/kraft/api.py` (`lifespan`, new `@app.websocket` route, imports)
- Test: `tests/test_ws.py` (extend)

**Interfaces:**
- Consumes: `Broadcaster` (Task 2), `Database.set_on_commit` (Task 1), `events.read_after`.
- Produces:
  - `WS /ws/events?after_seq=<int>` — sends each event as a JSON frame `{seq, work_item_id, type, payload, created_at}`. Replays `after_seq`-exclusive history up to the connect-time cursor, then live-streams. Closes if the client is `dropped`. Rejects a handshake whose `Origin` header host is not localhost.
  - `app.state.broadcaster` — the running `Broadcaster`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ws.py` (uses the existing `_client` helper from `tests/test_api.py` — import it):

```python
from pathlib import Path

from support.harness import fake_templates_dir, isolated_bd

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv(
        "KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE)))
    )
    import kraft.api as api

    from fastapi.testclient import TestClient

    return TestClient(api.app)


def test_ws_streams_live_events_after_connect(tmp_path, monkeypatch):
    with _api_client(tmp_path, monkeypatch) as client:
        with client.websocket_connect("/ws/events?after_seq=0") as ws:
            r = client.post(
                "/work-items",
                json={"title": "make the failing test pass", "repo": str(tmp_path)},
            )
            assert r.status_code == 201
            types = set()
            for _ in range(4):
                types.add(ws.receive_json()["type"])
            assert "work_item_created" in types


def test_ws_replays_history_then_reconnect_resumes_without_gap(tmp_path, monkeypatch):
    with _api_client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"title": "make the failing test pass", "repo": str(tmp_path)},
        ).json()["id"]
        # let a few events accrue
        _wait_events(client, wid, "chain_loaded")
        all_ev = client.get(f"/work-items/{wid}/events").json()
        cut = all_ev[len(all_ev) // 2]["seq"]

        with client.websocket_connect(f"/ws/events?after_seq={cut}") as ws:
            first = ws.receive_json()
            assert first["seq"] > cut  # exclusive replay, no gap below

        # reconnect from the last seq we saw: no duplicate, no gap
        last_seq = all_ev[-1]["seq"]
        with client.websocket_connect(f"/ws/events?after_seq={last_seq}") as ws:
            client.post(
                "/work-items", json={"title": "another one", "repo": str(tmp_path)}
            )
            nxt = ws.receive_json()
            assert nxt["seq"] > last_seq


def test_ws_rejects_cross_site_origin(tmp_path, monkeypatch):
    with _api_client(tmp_path, monkeypatch) as client:
        with pytest.raises(Exception):
            with client.websocket_connect(
                "/ws/events", headers={"origin": "https://evil.example"}
            ):
                pass


def _wait_events(client, wid, want, timeout=30):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ev = client.get(f"/work-items/{wid}/events").json()
        if any(e["type"] == want for e in ev):
            return ev
        time.sleep(0.2)
    raise AssertionError(f"{want} not seen")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_ws.py -q -k ws_`
Expected: FAIL — 403/404 on `/ws/events` (route missing).

- [ ] **Step 3: Implement in `src/kraft/api.py`**

Add imports:

```python
from urllib.parse import urlsplit

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from kraft.ws import Broadcaster
```

In `lifespan`, after the reattach/resume block and before `app.state.db = database` — actually set `app.state` fields first, then start the broadcaster just before `yield`:

```python
    app.state.db = database
    app.state.run_dirs = run_dirs
    app.state.registry = registry
    app.state.templates = templates
    app.state.policy = policy_obj
    app.state.invalid_policy = invalid_policy
    app.state.reattach_summary = summary

    broadcaster = Broadcaster(database)
    await broadcaster.start()
    database.set_on_commit(broadcaster.notify)
    app.state.broadcaster = broadcaster
    try:
        yield
    finally:
        database.set_on_commit(None)
        await broadcaster.stop()
        tasks = list(app.state.tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await database.close()
```

Add the helper + route (near the other routes):

```python
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1"}


def _origin_ok(origin: str | None) -> bool:
    if not origin:
        return True  # non-browser client
    return urlsplit(origin).hostname in _LOCAL_HOSTS


@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket, after_seq: int = 0):
    if not _origin_ok(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    st = websocket.app.state
    bc = st.broadcaster
    client = bc.register()
    live_start = bc.cursor
    await websocket.accept()
    try:
        for ev in st.db.read(lambda c: events.read_after(c, after_seq)):
            if ev["seq"] <= live_start:
                await websocket.send_json(ev)
        while True:
            if client.dropped:
                await websocket.close(code=1011)
                return
            try:
                ev = await asyncio.wait_for(client.queue.get(), timeout=1.0)
            except TimeoutError:
                continue
            if ev["seq"] <= live_start:
                continue
            await websocket.send_json(ev)
    except WebSocketDisconnect:
        pass
    finally:
        bc.unregister(client)
```

Note: the `asyncio.wait_for(..., 1.0)` loop lets the handler notice `client.dropped` and client disconnects promptly without a dedicated watcher task.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_ws.py -q`
Expected: PASS (all).

- [ ] **Step 5: Regression**

Run: `uv run pytest tests/test_api.py tests/test_api_shutdown.py -q`
Expected: PASS (shutdown test still clean — broadcaster stops before db close).

- [ ] **Step 6: Commit**

```bash
git add src/kraft/api.py tests/test_ws.py
git commit -m "feat(api): WS /ws/events transport with after_seq catch-up and origin guard"
```

---

## Task 4: `GET /work-items` list + cursor

**Files:**
- Modify: `src/kraft/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `app.state.db`, `app.state` (existing).
- Produces: `GET /work-items` → `{"items": [ {id, title, repo, status, chain_template, chain_definition, current_node_id, bead_id, created_at, updated_at} ], "cursor": <int>}`. `chain_definition` is parsed JSON. `cursor` is `COALESCE(MAX(seq), 0)` from `events`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api.py`:

```python
def test_list_work_items_shape_and_cursor(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        client.post("/work-items", json={"title": "make the failing test pass", "repo": str(tmp_path)})
        body = client.get("/work-items").json()
        assert set(body) == {"items", "cursor"}
        assert isinstance(body["cursor"], int) and body["cursor"] > 0
        item = body["items"][0]
        assert set(item) >= {
            "id", "title", "repo", "status", "chain_template",
            "chain_definition", "current_node_id", "bead_id", "created_at", "updated_at",
        }
        assert isinstance(item["chain_definition"], dict)
        assert item["chain_definition"]["nodes"][0]["id"]


def test_list_work_items_empty(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        body = client.get("/work-items").json()
        assert body == {"items": [], "cursor": 0}
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api.py -q -k list_work_items`
Expected: FAIL — 405 Method Not Allowed (only `POST /work-items` exists).

- [ ] **Step 3: Implement in `src/kraft/api.py`**

```python
@app.get("/work-items")
async def list_work_items(request: Request):
    st = request.app.state
    rows = st.db.read(
        lambda c: c.execute("SELECT * FROM work_items ORDER BY created_at").fetchall()
    )
    cursor = st.db.read(
        lambda c: c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
    )
    items = [
        {
            "id": r["id"],
            "title": r["title"],
            "repo": r["repo"],
            "status": r["status"],
            "chain_template": r["chain_template"],
            "chain_definition": json.loads(r["chain_definition"]),
            "current_node_id": r["current_node_id"],
            "bead_id": r["bead_id"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        }
        for r in rows
    ]
    return {"items": items, "cursor": cursor}
```

Place it **above** `@app.get("/work-items/{wid}")` (FastAPI route ordering: a static path before the parametrised sibling is clearest, though both resolve fine).

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_api.py -q -k list_work_items`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py tests/test_api.py
git commit -m "feat(api): GET /work-items list endpoint with event cursor"
```

---

## Task 5: `GET /templates`

**Files:**
- Modify: `src/kraft/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `app.state.templates` (a `TemplateSet` with `.valid: dict[str, Template]`).
- Produces: `GET /templates` → `[{"id": "<tid>"}]`, sorted by id, resolvable templates only.

- [ ] **Step 1: Write the failing test**

```python
def test_templates_lists_only_resolvable_sorted(tmp_path, monkeypatch):
    bad = fake_templates_dir(tmp_path, "claude")
    (bad / "broken.yaml").write_text(
        "id: broken\nnodes:\n  - {id: x, tasks: [on.nope], gate_after: null}\n"
    )
    with _client(tmp_path, monkeypatch, templates_dir=bad) as client:
        got = client.get("/templates").json()
        ids = [t["id"] for t in got]
        assert "broken" not in ids
        assert ids == sorted(ids)
        assert "quick-task" in ids
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api.py -q -k templates_lists`
Expected: FAIL — 404.

- [ ] **Step 3: Implement**

```python
@app.get("/templates")
async def list_templates(request: Request):
    return [{"id": tid} for tid in sorted(request.app.state.templates.valid)]
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_api.py -q -k templates`
Expected: PASS (new + existing template tests).

- [ ] **Step 5: Commit**

```bash
git add src/kraft/api.py tests/test_api.py
git commit -m "feat(api): GET /templates lists resolvable chain templates"
```

---

## Task 6: SPA static serving

**Files:**
- Modify: `src/kraft/api.py` (`lifespan` resolves the dist dir; new catch-all route; import)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `KRAFT_FRONTEND_DIST` env (default `<repo root>/frontend/dist`).
- Produces:
  - `app.state.frontend_dist: Path | None` — the resolved dir, or `None` if it does not exist.
  - `GET /{path:path}` catch-all (registered LAST): `404` if `frontend_dist` is `None`; serves `frontend_dist/<path>` when that file exists (covers `assets/*`); otherwise serves `frontend_dist/index.html` (SPA deep-link fallback). API routes declared earlier always win.

- [ ] **Step 1: Write the failing test**

```python
def test_spa_catchall_serves_index_when_dist_present(tmp_path, monkeypatch):
    dist = tmp_path / "fe-dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>kraft</title>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(dist))
    with _client(tmp_path, monkeypatch) as client:
        assert "<title>kraft</title>" in client.get("/").text
        # deep link -> index.html
        assert "<title>kraft</title>" in client.get("/work-items/abc123").text
        # real asset -> that file
        assert client.get("/assets/app.js").text == "console.log(1)"
        # API route still wins
        assert client.get("/health").json()["status"] in ("ok", "degraded")
        assert client.get("/work-items").json() == {"items": [], "cursor": 0}


def test_spa_catchall_404s_when_dist_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "nope"))
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/some/spa/route").status_code == 404
        assert client.get("/health").status_code == 200
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api.py -q -k spa_catchall`
Expected: FAIL — `/` returns 404 with no catch-all, or the deep link 404s.

- [ ] **Step 3: Implement in `src/kraft/api.py`**

Add import: `from fastapi.responses import FileResponse` is already imported. Add near `TEMPLATES_DIR`:

```python
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"
```

In `lifespan`, alongside the other `app.state` assignments:

```python
    dist = Path(os.environ.get("KRAFT_FRONTEND_DIST") or DEFAULT_FRONTEND_DIST)
    app.state.frontend_dist = dist if dist.is_dir() else None
```

At the very end of the module (after every other route):

```python
@app.get("/{path:path}")
async def spa(path: str, request: Request):
    dist = request.app.state.frontend_dist
    if dist is None:
        raise HTTPException(404, "not found")
    candidate = (dist / path).resolve()
    if dist.resolve() in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    return FileResponse(dist / "index.html")
```

The `dist.resolve() in candidate.parents` check keeps `../` traversal out.

**Plan correction (found during execution).** The catch-all above only sees
paths that match *no* earlier route. The SPA client route `/work-items/:id`
shadows `@app.get("/work-items/{wid}")`, so a deep-link to an unknown id 404s
from that API route before the catch-all is reached — and the plan's own
`test_spa_catchall_serves_index_when_dist_present` asserts `/work-items/abc123`
returns `index.html`. To satisfy that, also register a 404 handler that falls
back to the SPA shell for GET requests when a dist is present:

```python
@app.exception_handler(404)
async def _spa_deep_link(request: Request, exc: HTTPException):
    dist = getattr(request.app.state, "frontend_dist", None)
    if dist is not None and request.method == "GET":
        return FileResponse(dist / "index.html")
    return JSONResponse({"detail": exc.detail}, status_code=404)
```

Known ceiling (follow-up bead, resolve in 3B where the SPA + e2e exist to
validate): (1) this returns the HTML shell for *every* GET that 404s when a
frontend is deployed, so the SPA's own `api.ts` fetch client gets HTML instead
of a `{"detail": ...}` body on a genuine API miss (spec §3.4) — 3B's `api.ts`
should send `Accept: application/json` and/or this should content-negotiate on
`Sec-Fetch-Dest: document`; (2) deep-linking to a *valid* `/work-items/<id>`
still returns the API JSON (200), not the shell — real SPA deep-link support
needs a navigation-aware middleware ahead of API routing.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_api.py -q -k spa_catchall`
Expected: PASS.

- [ ] **Step 5: Full backend regression + lint**

Run: `uv run pytest -q -m "not slow and not e2e" && uv run ruff check . && uv run ruff format --check .`
Expected: all PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add src/kraft/api.py tests/test_api.py
git commit -m "feat(api): serve the built SPA with a deep-link catch-all"
```

---

## Task 7: Close out 3A

- [ ] **Step 1:** Run the full non-e2e suite once more: `uv run pytest -q -m "not e2e"` — expect all green (slow tests included).
- [ ] **Step 2:** `bd update Kraft-kif --notes="3A backend transport landed: Database.on_commit, kraft.ws.Broadcaster, WS /ws/events, GET /work-items, GET /templates, SPA catch-all. Ready for 3B frontend."`
- [ ] **Step 3:** Report handoff: changed files (`src/kraft/db.py`, `src/kraft/ws.py`, `src/kraft/api.py`, `tests/test_ws.py`, `tests/test_api.py`), test counts, and that 3B (`docs/superpowers/plans/2026-09-03-effort-3b-frontend-spa.md`) is unblocked.

---

## Self-Review

**Spec coverage (§2, §4, §5, §6.1–6.2, §8):**
- §2.1 `Database.on_commit` → Task 1. ✓
- §2.2 `Broadcaster` (queues, cursor, notify, fan-out, overflow drop) → Task 2. ✓
- §2.3 `WS /ws/events` (register→snapshot→catch-up→live, dedup boundary, origin check, disconnect) → Task 3. ✓
- §2.4 `GET /work-items` (shape, parsed `chain_definition`, no sessions, cursor) → Task 4. ✓
- §2.5 `GET /templates` (sorted, resolvable only) → Task 5. ✓
- §2.6 static serving (env override, exists-check, catch-all, API precedence, traversal guard) → Task 6. ✓
- §5 error handling: broken listener swallowed (Task 1 test), overflow→drop (Task 2 test), reconnect lossless (Task 3 test), dist absent (Task 6 test). ✓
- §6.1/§6.2 test list → covered across Tasks 1–6. ✓
- §8 interfaces: `Database.__init__`/`open` keyword-only `on_commit` + `set_on_commit`; `lifespan` constructs/starts/stops broadcaster; no schema/events/store/executor change. ✓

**Placeholder scan:** every code step has literal code; no TBD/"handle errors"/"similar to". ✓

**Type consistency:** `on_commit: Callable[[], None] | None` identical in `__init__`, `open`, `set_on_commit`. `Broadcaster(db, *, maxsize=1000)` matches Task 3 usage `Broadcaster(database)`. `Client.dropped` / `Client.queue` names consistent Task 2 ↔ Task 3. `app.state.broadcaster` / `app.state.frontend_dist` set in `lifespan`, read in routes. Event frame shape `{seq, work_item_id, type, payload, created_at}` = `events.read_after` output, used verbatim in Task 3. ✓
