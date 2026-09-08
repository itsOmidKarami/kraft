from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from starlette.websockets import WebSocket, WebSocketDisconnect
from support.harness import fake_templates_dir, isolated_bd, make_repo
from support.server import running_server

from kraft import db, events
from kraft.ws import Broadcaster

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    from fastapi.testclient import TestClient

    import kraft.api as api

    return TestClient(api.app, client=("127.0.0.1", 54321))


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


def test_broadcaster_drops_only_the_overflowing_client(tmp_path):
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
            for i in range(3):
                await database.write(
                    lambda c, i=i: events.append(c, "w1", "node_started", {"i": i})
                )
            await asyncio.sleep(0.05)
            # ok keeps up; slow never reads -> its queue is now full (maxsize=3)
            drained = [ok.queue.get_nowait() for _ in range(3)]
            for i in range(3, 6):
                await database.write(
                    lambda c, i=i: events.append(c, "w1", "node_started", {"i": i})
                )
            await asyncio.sleep(0.05)
            assert slow.dropped is True
            assert ok.dropped is False
            drained += [ok.queue.get_nowait() for _ in range(3)]
            assert [e["payload"]["i"] for e in drained] == list(range(6))
        finally:
            await bc.stop()
            await database.close()

    asyncio.run(scenario())


def test_broadcaster_survives_a_failing_fanout_iteration(tmp_path):
    async def scenario():
        bc_holder = {}
        database = await db.Database.open(
            tmp_path / "orchestrator.db", on_commit=lambda: bc_holder["bc"].notify()
        )
        bc = Broadcaster(database)
        bc_holder["bc"] = bc
        await bc.start()
        real_read = database.read
        state = {"boom": True}

        def flaky_read(fn):
            if state["boom"]:
                state["boom"] = False
                raise RuntimeError("transient read failure")
            return real_read(fn)

        database.read = flaky_read
        try:
            await _seed(database)
            client = bc.register()
            await database.write(lambda c: events.append(c, "w1", "node_started", {"n": "a"}))
            await asyncio.sleep(0.05)  # first wakeup: read raises, loop must survive
            await database.write(lambda c: events.append(c, "w1", "node_completed", {"n": "a"}))
            got = await asyncio.wait_for(client.queue.get(), 1)
            assert got["type"] == "node_started"  # nothing lost after the failed pass
            assert bc._task is not None and not bc._task.done()
        finally:
            database.read = real_read
            await bc.stop()
            await database.close()

    asyncio.run(scenario())


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
            client.post("/work-items", json={"title": "another one", "repo": str(tmp_path)})
            nxt = ws.receive_json()
            assert nxt["seq"] > last_seq


def test_ws_rejects_cross_site_origin(tmp_path, monkeypatch):
    with _api_client(tmp_path, monkeypatch) as client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/events", headers={"origin": "https://evil.example"}):
                pass
        assert exc.value.code == 1008


def _require_auth(client, monkeypatch):
    """Turn the auth gate on for an in-process app.

    `_requires_auth` keys off the address the process actually bound, so on
    loopback it is off. Making it true for real would mean binding a test server
    to 0.0.0.0 — a LAN-visible port for the length of a test run, which is not a
    trade a test suite should make. The wire protocol is covered by
    test_ws_events_delivered_under_real_uvicorn; what this asserts is the branch.
    """
    state = client.app.state
    monkeypatch.setattr(state, "bound_host", "10.0.0.5", raising=False)
    monkeypatch.setattr(state, "access", {**state.access, "password_hash": "x"}, raising=False)


def test_ws_events_refuses_a_client_with_no_credential(tmp_path, monkeypatch):
    with _api_client(tmp_path, monkeypatch) as client:
        _require_auth(client, monkeypatch)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/ws/events"):
                pass
        assert exc.value.code == 1008


def test_ws_events_accepts_the_mcp_bearer_token(tmp_path, monkeypatch):
    """Non-browser clients carry a bearer, not a cookie. `kraft watch` is one.

    The HTTP middleware has always accepted this token; the websocket accepting
    only the session cookie made the live stream the one endpoint a CLI could
    not reach.
    """
    with _api_client(tmp_path, monkeypatch) as client:
        _require_auth(client, monkeypatch)
        token = client.app.state.mcp_token
        assert token, "the server mints an MCP token at startup"
        with client.websocket_connect(
            "/ws/events", headers={"Authorization": f"Bearer {token}"}
        ) as ws:
            assert ws is not None  # the handshake completed; frame delivery is covered above


def test_ws_events_refuses_a_wrong_bearer(tmp_path, monkeypatch):
    with _api_client(tmp_path, monkeypatch) as client:
        _require_auth(client, monkeypatch)
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect(
                "/ws/events", headers={"Authorization": "Bearer not-the-token"}
            ):
                pass
        assert exc.value.code == 1008


def test_ws_no_gap_or_dup_when_events_land_in_register_window(tmp_path, monkeypatch):
    """Spec §6.1: events committed between register() and the catch-up read are
    delivered exactly once. Force that race by writing two events inside a
    monkeypatched WebSocket.accept()."""
    with _api_client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"title": "make the failing test pass", "repo": str(tmp_path)},
        ).json()["id"]
        _wait_events(client, wid, "chain_loaded")

        real_accept = WebSocket.accept
        fired = {"done": False}

        async def racing_accept(self, *a, **kw):
            if not fired["done"]:
                fired["done"] = True
                for name in ("race_a", "race_b"):
                    await self.app.state.db.write(
                        lambda c, name=name: events.append(c, wid, name, {})
                    )
            return await real_accept(self, *a, **kw)

        monkeypatch.setattr(WebSocket, "accept", racing_accept)

        seen_types: list[str] = []
        seqs: list[int] = []
        with client.websocket_connect("/ws/events?after_seq=0") as ws:
            for _ in range(60):
                ev = ws.receive_json()
                seqs.append(ev["seq"])
                seen_types.append(ev["type"])
                if {"race_a", "race_b"} <= set(seen_types):
                    break

        assert {"race_a", "race_b"} <= set(seen_types)
        assert seen_types.count("race_a") == 1 and seen_types.count("race_b") == 1
        # strictly increasing, contiguous from the first replayed seq (no gap, no dup)
        assert seqs == list(range(seqs[0], seqs[0] + len(seqs)))


def _wait_events(client, wid, want, timeout=30):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ev = client.get(f"/work-items/{wid}/events").json()
        if any(e["type"] == want for e in ev):
            return ev
        time.sleep(0.2)
    raise AssertionError(f"{want} not seen")


@pytest.mark.slow
def test_ws_events_delivered_under_real_uvicorn(tmp_path):
    """The TestClient does WS in-process; this proves a real uvicorn handshake to
    /ws/events works (needs the `websockets` protocol lib) and streams frames."""
    from websockets.sync.client import connect

    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    with running_server(run_dir=tmp_path / "run", templates_dir=templates, bd_cwd=tracker) as srv:
        with connect(f"ws://127.0.0.1:{srv.port}/ws/events?after_seq=0") as ws:
            r = srv.client.post(
                "/work-items",
                json={"title": "make the failing test pass", "repo": str(repo)},
            )
            assert r.status_code == 201, r.text
            ev = json.loads(ws.recv(timeout=15))
            assert "type" in ev
