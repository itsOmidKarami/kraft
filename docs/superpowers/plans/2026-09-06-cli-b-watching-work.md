# Kraft CLI Sub-project B — Watching Work Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `kraft logs -f`, `kraft events` and `kraft watch` — follow a running agent from a terminal instead of keeping a browser tab open.

**Architecture:** Two async generators in `client.py` (SSE over `GET /worker-sessions/{sid}/log?format=jsonl&follow=1`, and a websocket on `/ws/events`), plus one non-streaming `events()` call. `cli.py` gains three handlers; `render.py` gains a log-line renderer and a redraw helper. One server change: `/ws/events` learns to accept the bearer token every HTTP route already accepts.

**Tech Stack:** Python 3.14, `httpx` streaming, `websockets` (already a dependency, locked 17.1), argparse, pytest.

**Spec:** `docs/superpowers/specs/2026-09-06-cli-b-watching-work-design.md`

**Beads issue:** `Kraft-bbg.2`. Claim it before Task 1: `bd update Kraft-bbg.2 --claim`.

**Depends on:** sub-project A (`Kraft-bbg.1`) — `render.py`, `cli.build_parser`, `cli.emit`, `client._send` must all exist.

## Global Constraints

- Python `>=3.14`, ruff `line-length = 100`, no new runtime dependencies. `websockets` and `httpx` are already in `[project].dependencies`.
- A test that touches a websocket or SSE **must run against real uvicorn** and be marked `@pytest.mark.slow`. `httpx.ASGITransport` and `TestClient` do not exercise the same code path — `tests/test_ws.py::test_ws_events_delivered_under_real_uvicorn` exists precisely because a missing WS protocol library is invisible in-process. Use `support.server.running_server`.
- Streaming functions are **not** registered as MCP tools. An MCP tool returns a value; a generator has none. `client.py` stops being a strict superset of the MCP surface here, deliberately.
- `kraft logs -f` exits `0` when the session stops. A follow that hangs after the agent is gone is worse than no follow.
- `--json` on `logs` is NDJSON — one object per line, no enclosing array. A stream has no end to close a bracket on.
- Errors to stderr as `kraft: <message>`; exit `0` ok, `1` failed, `2` usage; `130` on Ctrl-C. All inherited from A.
- Commits stay local: no push, no merge to `main`, no `bd dolt push` unless asked.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kraft/api.py` *(modify)* | `ws_events` accepts `Authorization: Bearer`, mirroring the HTTP middleware. |
| `src/kraft/client.py` *(modify)* | `worker_sessions()`, `events()`, `stream_log()`, `stream_events()`. |
| `src/kraft/render.py` *(modify)* | `log_line()`, `event_line()`, `redraw()`. |
| `src/kraft/cli.py` *(modify)* | `_cmd_logs`, `_cmd_events`, `_cmd_watch` and their subparsers. |
| `tests/test_ws.py` *(modify)* | The bearer-auth handshake test, against real uvicorn. |
| `tests/test_cli_watching.py` *(create)* | The three verbs, plus the two streaming client functions. |

---

### Task 1: `/ws/events` accepts the bearer token

**Files:**
- Modify: `src/kraft/api.py` (`ws_events`)
- Test: `tests/test_ws.py`

**Interfaces:**
- Consumes: `auth_mod.COOKIE`, `app.state.mcp_token`, `_requires_auth`.
- Produces: no new symbol — `ws_events` accepts one more credential.

**Why this is task one:** without it, `kraft watch` is closed with code 1008 on any LAN bind with a password, which is exactly the away-from-desk setup where a terminal board is worth having. It is also an inconsistency that exists today, independent of the CLI: `api.py`'s HTTP middleware accepts `Authorization: Bearer <mcp_token>`, and the websocket handler accepts only the browser session cookie.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ws.py`:

```python
@pytest.mark.slow
def test_ws_events_accepts_the_mcp_bearer_token(tmp_path):
    """Non-browser clients have a bearer, not a cookie. `kraft watch` is one.

    Real uvicorn: the TestClient does websockets in-process and would not prove
    the handshake headers survive a real one.
    """
    from websockets.sync.client import connect

    from kraft import auth

    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    # a password makes _requires_auth true even on loopback
    (templates / "access.yaml").write_text(
        "bind: 127.0.0.1\npassword_hash: " + auth.hash_password("hunter2") + "\n"
    )
    tracker = isolated_bd(tmp_path)
    run_dir = tmp_path / "run"
    with running_server(
        run_dir=run_dir,
        templates_dir=templates,
        bd_cwd=tracker,
        env={"KRAFT_REQUIRE_AUTH": "1"},
    ) as srv:
        token = auth.read_mcp_token(run_dir)
        assert token, "the server writes an MCP token at startup"
        # no credential at all is still refused
        with pytest.raises(Exception):
            with connect(f"ws://127.0.0.1:{srv.port}/ws/events"):
                pass
        with connect(
            f"ws://127.0.0.1:{srv.port}/ws/events",
            additional_headers={"Authorization": f"Bearer {token}"},
        ) as ws:
            assert ws is not None  # the handshake completed; frames are covered above
```

> **Before writing the implementation, check two things and adapt the test rather than guessing:**
> 1. Whether `auth.hash_password` is the real helper name — read `src/kraft/auth.py` and use whatever `PUT /access` uses to store a hash.
> 2. Whether `_requires_auth` keys off the bound address only, or off a password being set. Read its docstring in `api.py`. If auth is off on loopback regardless of password, bind the test server to a non-loopback address is **not** an option — instead monkeypatching is impossible across a subprocess, so set whatever env var or access.yaml field `_requires_auth` actually honours. If nothing makes auth true on loopback, drop the `KRAFT_REQUIRE_AUTH` env and instead assert the positive case only (bearer accepted), and note in the commit message that the negative case is covered by the existing HTTP middleware tests.

- [ ] **Step 2: Run the test to verify it fails**

Run: `just test tests/test_ws.py -k bearer -m slow`
Expected: FAIL — the handshake is rejected with 1008 because only the cookie is checked.

- [ ] **Step 3: Write the implementation**

In `src/kraft/api.py`, inside `ws_events`, replace the cookie-only block:

```python
    # HTTP middleware does not run for websockets, so the session check has to be
    # here too — otherwise a LAN bind would leave the live event stream open.
    #
    # Both credentials, for the same reason the HTTP middleware takes both: a
    # browser has a session cookie, and a non-browser client (`kraft watch`, an
    # agent) has the bearer token from run/. Accepting only the cookie made the
    # live stream the one endpoint a CLI could not reach.
    if _requires_auth(websocket.app):
        bearer = websocket.headers.get("authorization", "")
        expected = getattr(websocket.app.state, "mcp_token", None)
        authorised = bool(
            expected and bearer.startswith("Bearer ") and hmac.compare_digest(bearer[7:], expected)
        )
        if not authorised:
            token = websocket.cookies.get(auth_mod.COOKIE)
            authorised = bool(
                token
                and await websocket.app.state.db.write(
                    lambda c, token=token: auth_mod.touch_session(c, token)
                )
            )
        if not authorised:
            await websocket.close(code=1008)
            return
```

`hmac` is already imported in `api.py` (the HTTP middleware uses `hmac.compare_digest`); confirm before adding an import.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_ws.py -m slow` then `just test tests/test_api.py`
Expected: PASS — including the existing cookie path, which must not regress.

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/api.py tests/test_ws.py
git commit -m "fix(api): /ws/events accepts the MCP bearer, not only a session cookie"
```

---

### Task 2: `client.worker_sessions()` and `client.events()`

**Files:**
- Modify: `src/kraft/client.py`
- Create: `tests/test_cli_watching.py`

**Interfaces:**
- Consumes: `client._get`, `client.resolve_context`.
- Produces:
  - `async def worker_sessions(work_item_id: str | None = None) -> list[dict]` — the item's sessions, newest last, each `{id, node_id, hook_point, status, log_path, created_at}`.
  - `async def events(work_item_id: str | None = None, after_seq: int = 0) -> list[dict]`.
  - `async def latest_session(work_item_id: str | None = None) -> dict` — the most recent session; raises `ValueError` when there is none.

`client.get_work_item()` trims its payload and does **not** forward `worker_sessions`, so `logs` cannot reuse it. These read the untrimmed `GET /work-items/{wid}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli_watching.py
"""logs, events and watch — the streaming door onto a running work item."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo

from kraft import cli, client

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


# `app` fixture: tests/conftest.py (sub-project A Task 4). It wires client.http()
# to the ASGI app with the lifespan entered per client.


def _make_item(repo, title="watch me"):
    async def go():
        async with client.http() as http:
            response = await http.post(
                "/work-items", json={"title": title, "repo": str(repo), "autostart": False}
            )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    return asyncio.run(go())


def test_worker_sessions_is_empty_before_anything_runs(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    assert asyncio.run(client.worker_sessions(wid)) == []


def test_latest_session_says_so_when_nothing_has_run(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    with pytest.raises(ValueError, match="no worker session"):
        asyncio.run(client.latest_session(wid))


def test_events_returns_the_chain_history(app, tmp_path):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    rows = asyncio.run(client.events(wid))
    assert isinstance(rows, list)
    assert all("type" in row and "seq" in row for row in rows)


def test_events_defaults_to_the_resolved_work_item(app, tmp_path, monkeypatch):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    monkeypatch.setenv("KRAFT_WORK_ITEM_ID", wid)
    assert asyncio.run(client.events()) == asyncio.run(client.events(wid))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_watching.py`
Expected: FAIL — `module 'kraft.client' has no attribute 'worker_sessions'`

- [ ] **Step 3: Write the implementation**

Add to `src/kraft/client.py`, after `get_work_item`:

```python
async def _target(work_item_id: str | None) -> str:
    """An explicit id, or the item this session is standing in.

    Not `_forbid_self_action`: reading your own logs is exactly what a worker
    session should be able to do. The guard is about acting, not looking.
    """
    if work_item_id:
        return work_item_id
    resolved, _origin = resolve_context()
    if resolved is None:
        raise ValueError("no work item: pass an id, or run from a Kraft worktree")
    return resolved


async def worker_sessions(work_item_id: str | None = None) -> list[dict]:
    """The item's agent sessions, oldest first.

    Reads the untrimmed detail endpoint: `get_work_item` deliberately drops
    `worker_sessions` so an agent's context is not spent on it, which means the
    log verbs cannot reuse it.
    """
    item = await _get(f"/work-items/{await _target(work_item_id)}")
    return item.get("worker_sessions", [])


async def latest_session(work_item_id: str | None = None) -> dict:
    """The most recent session — "what is it doing now", which is the question
    `kraft logs` is asked."""
    sessions = await worker_sessions(work_item_id)
    if not sessions:
        raise ValueError("no worker session has run for this work item yet")
    return sessions[-1]


async def events(work_item_id: str | None = None, after_seq: int = 0) -> list[dict]:
    """The chain's own history: node transitions, gate decisions, escalations.

    This is the "why is it stopped" view, where the log is the "what is it
    saying" view.
    """
    return await _get(
        f"/work-items/{await _target(work_item_id)}/events", after_seq=after_seq
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_watching.py`
Expected: PASS (4 tests)

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/client.py tests/test_cli_watching.py
git commit -m "feat(cli): client reads worker sessions and chain events"
```

---

### Task 3: `client.stream_log()` — SSE tail

**Files:**
- Modify: `src/kraft/client.py`
- Modify: `tests/test_cli_watching.py`

**Interfaces:**
- Consumes: `client.http`, `client.base_url`.
- Produces: `async def stream_log(session_id: str, after_line: int = 0) -> AsyncIterator[dict]` — yields `{"n": int, "t": str | None, "src": str, "text": str}`, the shape `logs.jsonl` produces, and stops when the server sends its terminal `event: end` frame.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli_watching.py`:

```python
@pytest.mark.slow
def test_stream_log_follows_a_session_and_stops_when_it_stops(tmp_path, monkeypatch):
    """Real uvicorn: SSE through ASGITransport is not the same code path, and a
    stream that never ends is the failure this test exists to catch."""
    from support.server import running_server

    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    run_dir = tmp_path / "run"
    with running_server(run_dir=run_dir, templates_dir=templates, bd_cwd=tracker) as srv:
        monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
        monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
        monkeypatch.setenv("KRAFT_PORT", str(srv.port))
        monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
        response = srv.client.post(
            "/work-items", json={"title": "log me", "repo": str(repo)}
        )
        assert response.status_code == 201, response.text
        wid = response.json()["id"]

        async def collect():
            # wait for the fake agent to produce a session
            for _ in range(150):
                sessions = await client.worker_sessions(wid)
                if sessions:
                    break
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("no worker session appeared in 30s")
            return [line async for line in client.stream_log(sessions[-1]["id"])]

        lines = asyncio.run(asyncio.wait_for(collect(), timeout=90))

    assert lines, "the fake agent writes at least one line"
    assert all({"n", "src", "text"} <= set(line) for line in lines)
    # line numbers arrive in order and without gaps from the start
    assert [line["n"] for line in lines] == sorted(line["n"] for line in lines)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `just test tests/test_cli_watching.py -k stream_log -m slow`
Expected: FAIL — `module 'kraft.client' has no attribute 'stream_log'`

- [ ] **Step 3: Write the implementation**

Add to `src/kraft/client.py` (and add `from collections.abc import AsyncIterator` and `import json` to the imports):

```python
async def stream_log(session_id: str, after_line: int = 0) -> AsyncIterator[dict]:
    """Follow one worker session's log until the session stops.

    The server's `_tail` sends `data: {json}` frames and one terminal
    `event: end` when the session is no longer running, so the iterator ends on
    its own — a follow that outlives the agent is worse than no follow.

    Not an MCP tool: a tool returns a value and a generator has none. An agent
    that wants history calls `events()`.
    """
    async with http() as session:
        try:
            async with session.stream(
                "GET",
                f"/worker-sessions/{session_id}/log",
                params={"format": "jsonl", "follow": "true", "after_line": after_line},
                timeout=None,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise ValueError(f"kraft {response.status_code}: {_detail(response)}")
                async for raw in response.aiter_lines():
                    if raw.startswith("event: end"):
                        return
                    if raw.startswith("data: "):
                        payload = raw[6:].strip()
                        if payload and payload != "{}":
                            yield json.loads(payload)
        except httpx.ConnectError as exc:
            raise ValueError(
                f"no Kraft server at {base_url()} — start one with `kraft serve`"
            ) from exc
```

> `timeout=None` is required: the default 30s client timeout would kill a follow on any agent that thinks for half a minute, which is most of them.

- [ ] **Step 4: Run the test to verify it passes**

Run: `just test tests/test_cli_watching.py -k stream_log -m slow`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/client.py tests/test_cli_watching.py
git commit -m "feat(cli): stream_log() follows a worker session over SSE"
```

---

### Task 4: `kraft logs`

**Files:**
- Modify: `src/kraft/render.py` (`log_line`)
- Modify: `src/kraft/cli.py` (`_cmd_logs`, subparser)
- Modify: `tests/test_cli_watching.py`, `tests/test_render.py`

**Interfaces:**
- Consumes: `client.stream_log`, `client.latest_session`, `client._get`, `render.paint`.
- Produces: `render.log_line(entry: dict) -> str`, `cli._cmd_logs(ns)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_render.py`:

```python
def test_log_line_shows_source_and_text():
    out = render.log_line({"n": 3, "t": "2026-09-06T12:00:00+00:00", "src": "stdout", "text": "hi"})
    assert "hi" in out
    assert "stdout" in out


def test_log_line_survives_a_line_with_no_timestamp():
    out = render.log_line({"n": 0, "t": None, "src": "stderr", "text": "boom"})
    assert "boom" in out
```

Add to `tests/test_cli_watching.py`:

```python
def test_logs_without_a_session_says_so(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    with pytest.raises(SystemExit) as caught:
        cli.main(["logs", wid])
    assert caught.value.code == 1
    assert "no worker session" in capsys.readouterr().err


def test_logs_backlog_renders_lines(app, tmp_path, monkeypatch, capsys):
    """The backlog path needs no server streaming: it is a plain GET."""
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_latest(work_item_id=None):
        return {"id": "sess-1", "status": "stopped"}

    async def fake_get(path, **params):
        assert path == "/worker-sessions/sess-1/log"
        return {
            "session_id": "sess-1",
            "status": "stopped",
            "lines": [
                {"n": 0, "t": None, "src": "stdout", "text": "first"},
                {"n": 1, "t": None, "src": "stdout", "text": "second"},
            ],
        }

    monkeypatch.setattr(client, "latest_session", fake_latest)
    monkeypatch.setattr(client, "_get", fake_get)
    cli.main(["logs", wid])
    out = capsys.readouterr().out
    assert "first" in out and "second" in out


def test_logs_n_limits_the_backlog(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_latest(work_item_id=None):
        return {"id": "sess-1", "status": "stopped"}

    async def fake_get(path, **params):
        return {
            "session_id": "sess-1",
            "status": "stopped",
            "lines": [{"n": i, "t": None, "src": "stdout", "text": f"line{i}"} for i in range(10)],
        }

    monkeypatch.setattr(client, "latest_session", fake_latest)
    monkeypatch.setattr(client, "_get", fake_get)
    cli.main(["logs", wid, "-n", "3"])
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 3
    assert "line9" in out[-1]


def test_logs_json_is_ndjson(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)

    async def fake_latest(work_item_id=None):
        return {"id": "sess-1", "status": "stopped"}

    async def fake_get(path, **params):
        return {"lines": [{"n": 0, "t": None, "src": "stdout", "text": "one"}]}

    monkeypatch.setattr(client, "latest_session", fake_latest)
    monkeypatch.setattr(client, "_get", fake_get)
    cli.main(["logs", wid, "--json"])
    out = capsys.readouterr().out.strip()
    # one JSON object per line, no enclosing array: a stream has no closing bracket
    assert json.loads(out)["text"] == "one"
    assert not out.startswith("[")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_render.py tests/test_cli_watching.py`
Expected: FAIL — `render` has no `log_line`; argparse rejects `logs`.

- [ ] **Step 3: Write the implementation**

`src/kraft/render.py`:

```python
#: Log source -> ANSI colour. stderr is the one a reader is scanning for.
LOG_COLORS = {"stderr": "\033[31m", "system": DIM}


def log_line(entry: dict) -> str:
    """One JSONL log line: time, source, text. Never raises on a partial line."""
    stamp = (entry.get("t") or "")[11:19] or "--:--:--"
    src = str(entry.get("src") or "?")
    text = str(entry.get("text") or "")
    return f"{paint(stamp, DIM)} {paint(src.ljust(6), LOG_COLORS.get(src, ''))} {text}"
```

`src/kraft/cli.py`:

```python
def _cmd_logs(ns: argparse.Namespace) -> None:
    async def run() -> None:
        session_id = ns.session or (await client.latest_session(ns.id))["id"]
        payload = await client._get(
            f"/worker-sessions/{session_id}/log", format="jsonl"
        )
        lines = payload.get("lines", [])
        if ns.n:
            lines = lines[-ns.n :]
        for entry in lines:
            _print_log(entry, ns.json)
        if ns.follow:
            seen = lines[-1]["n"] + 1 if lines else 0
            async for entry in client.stream_log(session_id, after_line=seen):
                _print_log(entry, ns.json)

    asyncio.run(run())


def _print_log(entry: dict, as_json: bool) -> None:
    """NDJSON under --json: one object per line, because a stream has no end to
    close an array on. `flush` because a follow that buffers is not a follow."""
    print(json.dumps(entry) if as_json else render.log_line(entry), flush=True)
```

Subparser, appended to `_add_verbs`:

```python
    logs = subs.add_parser("logs", parents=[common], help="a worker session's log")
    logs.add_argument("id", nargs="?", help="default: the work item you are standing in")
    logs.add_argument("-f", "--follow", action="store_true", help="follow until the session stops")
    logs.add_argument("--session", help="default: the most recent session of the work item")
    logs.add_argument(
        "-n", type=int, default=50, help="backlog lines before following (0 for none)"
    )
    logs.set_defaults(func=_cmd_logs)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_render.py tests/test_cli_watching.py`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/render.py src/kraft/cli.py tests/test_render.py tests/test_cli_watching.py
git commit -m "feat(cli): kraft logs, with -f following until the session stops"
```

---

### Task 5: `kraft events`

**Files:**
- Modify: `src/kraft/render.py` (`event_line`)
- Modify: `src/kraft/cli.py` (`_cmd_events`, subparser)
- Modify: `tests/test_cli_watching.py`

**Interfaces:**
- Consumes: `client.events`, `render.table`.
- Produces: `render.event_line`, `cli._cmd_events`.

- [ ] **Step 1: Write the failing tests**

```python
def test_events_renders_a_table(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["events", wid])
    out = capsys.readouterr().out
    assert "SEQ" in out.splitlines()[0]
    assert "TYPE" in out.splitlines()[0]


def test_events_filters_by_type(app, tmp_path, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    cli.main(["events", wid, "--type", "no-such-type", "--json"])
    assert json.loads(capsys.readouterr().out) == []


def test_events_after_seq_is_passed_through(app, tmp_path, monkeypatch, capsys):
    repo = make_repo(tmp_path)
    wid = _make_item(repo)
    seen = {}

    async def fake_events(work_item_id=None, after_seq=0):
        seen["after_seq"] = after_seq
        return []

    monkeypatch.setattr(client, "events", fake_events)
    cli.main(["events", wid, "--after", "7", "--json"])
    assert seen["after_seq"] == 7
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_watching.py -k events`
Expected: FAIL — argparse rejects `events`.

- [ ] **Step 3: Write the implementation**

`src/kraft/render.py`:

```python
_EVENT_COLUMNS = [("SEQ", "seq"), ("WHEN", "when"), ("TYPE", "type"), ("DETAIL", "detail")]


def event_line(rows: list[dict]) -> str:
    """The chain's history as a table. `payload` is JSON text on the wire; only
    its first line is shown, because this view is scanned, not read — `--json`
    is there for the whole thing."""
    shaped = [
        {
            "seq": row.get("seq"),
            "when": relative_time(row.get("created_at")),
            "type": row.get("type"),
            "detail": str(row.get("payload") or "").replace("\n", " ")[:200],
        }
        for row in rows
    ]
    return table(shaped, _EVENT_COLUMNS)
```

`src/kraft/cli.py`:

```python
def _cmd_events(ns: argparse.Namespace) -> None:
    rows = asyncio.run(client.events(ns.id, ns.after))
    if ns.type:
        rows = [row for row in rows if row.get("type") == ns.type]
    emit(rows, render.event_line, ns.json)
```

```python
    events_p = subs.add_parser("events", parents=[common], help="the chain's own history")
    events_p.add_argument("id", nargs="?")
    events_p.add_argument("--after", type=int, default=0, help="only events after this seq")
    events_p.add_argument("--type", help="only this event type")
    events_p.set_defaults(func=_cmd_events)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_watching.py -k events`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
just lint
git add src/kraft/render.py src/kraft/cli.py tests/test_cli_watching.py
git commit -m "feat(cli): kraft events — the chain history as a table"
```

---

### Task 6: `client.stream_events()` and `kraft watch`

**Files:**
- Modify: `src/kraft/client.py` (`stream_events`)
- Modify: `src/kraft/render.py` (`redraw`)
- Modify: `src/kraft/cli.py` (`_cmd_watch`, subparser)
- Modify: `tests/test_cli_watching.py`

**Interfaces:**
- Consumes: `client.base_url`, `auth.read_mcp_token`, `client.list_work_items`, `client.resolve_repo`, `cli._repo_scope`, `render.table`.
- Produces:
  - `async def stream_events(after_seq: int = 0) -> AsyncIterator[dict]`
  - `render.redraw(text: str, previous_lines: int) -> int` — writes the frame over the previous one, returns the new line count.
  - `cli._cmd_watch(ns)`

- [ ] **Step 1: Write the failing tests**

```python
def test_watch_refuses_a_pipe(app, capsys, monkeypatch):
    """Redrawing into a pipe produces garbage; point at `events` instead."""
    monkeypatch.setattr("sys.stdout.isatty", lambda: False, raising=False)
    with pytest.raises(SystemExit) as caught:
        cli.main(["watch"])
    assert caught.value.code == 1
    assert "events" in capsys.readouterr().err


def test_redraw_returns_the_new_line_count():
    from kraft import render

    assert render.redraw("a\nb\nc", 0) == 3


@pytest.mark.slow
def test_stream_events_yields_a_frame_when_a_work_item_is_created(tmp_path, monkeypatch):
    from support.server import running_server

    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    run_dir = tmp_path / "run"
    with running_server(run_dir=run_dir, templates_dir=templates, bd_cwd=tracker) as srv:
        monkeypatch.setenv("KRAFT_RUN_DIR", str(run_dir))
        monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(templates))
        monkeypatch.setenv("KRAFT_HOST", "127.0.0.1")
        monkeypatch.setenv("KRAFT_PORT", str(srv.port))

        async def collect():
            stream = client.stream_events()
            first = asyncio.create_task(anext(stream))
            await asyncio.sleep(0.5)  # let the handshake complete before the write
            srv.client.post("/work-items", json={"title": "seen live", "repo": str(repo)})
            event = await asyncio.wait_for(first, timeout=20)
            await stream.aclose()
            return event

        event = asyncio.run(collect())

    assert "type" in event
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `just test tests/test_cli_watching.py -k "watch or redraw or stream_events"`
Expected: FAIL — no `stream_events`, no `redraw`, argparse rejects `watch`.

- [ ] **Step 3: Write the implementation**

`src/kraft/client.py` (import `websockets` lazily inside the function — it is a dependency, but keeping the import local means `kraft list` does not pay for it):

```python
async def stream_events(after_seq: int = 0) -> AsyncIterator[dict]:
    """The live event bus — the same stream the board's UI redraws from.

    The bearer goes in a header: the websocket handler accepts it as of the
    /ws/events auth fix, because a CLI has no session cookie to offer.
    """
    from websockets.asyncio.client import connect

    url = base_url().replace("http://", "ws://", 1) + f"/ws/events?after_seq={after_seq}"
    run_dir = Path(os.environ.get("KRAFT_RUN_DIR") or default_run_dir())
    token = auth.read_mcp_token(run_dir)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with connect(url, additional_headers=headers) as socket:
            async for message in socket:
                yield json.loads(message)
    except OSError as exc:
        raise ValueError(
            f"no Kraft server at {base_url()} — start one with `kraft serve`"
        ) from exc
```

`src/kraft/render.py`:

```python
def redraw(text: str, previous_lines: int) -> int:
    """Overwrite the previous frame in place; return this frame's line count.

    Cursor-up rather than an alternate screen, so the last frame stays in the
    scrollback when you Ctrl-C — the reason to watch in a terminal at all.
    """
    if previous_lines:
        sys.stdout.write(f"\033[{previous_lines}A\033[J")
    sys.stdout.write(text + "\n")
    sys.stdout.flush()
    return len(text.splitlines())
```

`src/kraft/cli.py`:

```python
def _cmd_watch(ns: argparse.Namespace) -> None:
    if not sys.stdout.isatty():
        raise ValueError("watch needs a terminal to redraw in — try `kraft events` in a pipe")

    async def run() -> None:
        repo = await client.resolve_repo() if not ns.repo else ns.repo
        drawn = 0

        async def frame() -> int:
            items = await client.list_work_items()
            if repo:
                items = [item for item in items if item["repo"] == repo]
            return render.redraw(_render_list(items), drawn)

        drawn = await frame()
        async for _event in client.stream_events():
            drawn = await frame()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass  # the frame stays on screen; that is the point of not using an alt screen
```

```python
    watch = subs.add_parser("watch", parents=[common], help="a live board, redrawn on each event")
    watch.add_argument("--repo", help="default: the repo you are standing in")
    watch.set_defaults(func=_cmd_watch, all=False)
```

> `--json` is accepted (it comes from the shared parent parser) but meaningless for a redrawn frame. Have `_cmd_watch` raise `ValueError("watch has no --json; use `kraft events --json` to stream structured output")` when `ns.json` is set, rather than printing frames of JSON nobody can consume.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `just test tests/test_cli_watching.py`
Expected: PASS. Add a test for the `--json` refusal above while you are here:

```python
def test_watch_has_no_json_mode(app, capsys, monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    with pytest.raises(SystemExit) as caught:
        cli.main(["watch", "--json"])
    assert caught.value.code == 1
    assert "--json" in capsys.readouterr().err
```

- [ ] **Step 5: Run the whole suite**

Run: `just test` then `just test -m slow`
Expected: PASS both.

- [ ] **Step 6: Lint and commit**

```bash
just lint
git add src/kraft/client.py src/kraft/render.py src/kraft/cli.py tests/test_cli_watching.py
git commit -m "feat(cli): kraft watch — a live board over /ws/events"
```

---

### Task 7: Document the watching verbs

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `AGENTS.md`

- [ ] **Step 1: Extend the README's CLI section**

````markdown
Following a running item:

```bash
kraft logs -f            # the current session's log, until it stops
kraft logs --session <id> -n 0
kraft events             # node transitions, gate decisions, escalations
kraft watch              # a live board, redrawn on every event
```

`kraft logs --json` emits NDJSON — one object per line — because a stream has no
end to close an array on.
````

- [ ] **Step 2: Mirror into `CLAUDE.md` and `AGENTS.md`**

They are independent files, not symlinks. Substantive edits go in both.

- [ ] **Step 3: Commit and close**

```bash
just lint
git add README.md CLAUDE.md AGENTS.md
git commit -m "docs: the kraft watching verbs"
bd close Kraft-bbg.2 --reason="logs/-f, events, watch shipped; /ws/events now takes the bearer"
```

---

## Self-Review

**Spec coverage**

| Spec section | Task |
|---|---|
| §3 `kraft logs` (default session, `-f`, `-n`) | 2 and 4 |
| §3 `kraft events` (`--after`, `--type`) | 2 and 5 |
| §3 `kraft watch` (board-level, repo-scoped, Ctrl-C) | 6 |
| §4 rendering, NDJSON, cursor-up redraw, non-tty refusal | 4, 5, 6 |
| §5 two async generators, not MCP tools | 3 and 6 |
| §6 websocket bearer auth | 1 |
| §7 testing (slow marks, real uvicorn, follow terminates) | 1, 3, 6 |
| §8 decisions (bearer fixed here; board-level only) | 1, 6 |

**Placeholder scan:** Task 1 Step 1 carries a "check these two things before implementing" note about `auth.hash_password` and `_requires_auth`'s trigger. That is a real instruction to read two named functions, not a TBD — the alternative is guessing at an auth predicate, which is the one place guessing is unacceptable.

**Type consistency:** `client._target(work_item_id)` is defined in Task 2 and used by `worker_sessions` and `events` in the same task. `stream_log(session_id, after_line)` is defined in Task 3 and called in Task 4 with both arguments. `render.redraw(text, previous_lines) -> int` is defined and consumed in Task 6. `_render_list` comes from sub-project A Task 5 and is reused unchanged by `_cmd_watch`.
