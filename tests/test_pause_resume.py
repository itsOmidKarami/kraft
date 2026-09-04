"""Pause / steer / resume (02 §10.2, design 4c).

An agent CLI is a one-shot subprocess with no stdin, so pausing means killing
the current attempt and resuming means launching a fresh one — optionally with
a human's note leading its prompt.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient
from support.harness import fake_templates_dir, isolated_bd, make_repo

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    import kraft.api as api

    return TestClient(api.app)


def _wait(fn, what, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        got = fn()
        if got:
            return got
        time.sleep(0.15)
    raise AssertionError(f"timed out waiting for {what}")


def _running_agent(client, wid):
    def check():
        rows = client.get(f"/work-items/{wid}").json()["worker_sessions"]
        return next(
            (
                s
                for s in rows
                if s["hook_point"] == "on.implementation.start" and s["status"] == "running"
            ),
            None,
        )

    return _wait(check, "a running agent session")


def test_pause_then_resume_with_a_steer_relaunches_the_task(tmp_path, monkeypatch):
    # a slow agent gives the test a live session to interrupt
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "30")
    repo = make_repo(tmp_path)
    prompts = tmp_path / "prompts.txt"
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_PROMPT_LOG", str(prompts))

    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "pause me", "chain_template": "quick-task"},
        ).json()["id"]
        session = _running_agent(client, wid)

        r = client.post(f"/work-items/{wid}/pause", json={})
        assert r.status_code == 200
        assert r.json()["paused_sessions"] == [session["id"]]

        item = _wait(
            lambda: (lambda b: b if b["status"] == "paused" else None)(
                client.get(f"/work-items/{wid}").json()
            ),
            "the item to read paused",
        )
        paused = next(s for s in item["worker_sessions"] if s["id"] == session["id"])
        # the SIGTERM's non-zero exit must not re-resolve the row as a failure
        assert paused["status"] == "paused"
        types = [e["type"] for e in client.get(f"/work-items/{wid}/events").json()]
        assert "pause_requested" in types and "worker_session_paused" in types

        # pausing twice is a client error, not a second kill
        assert client.post(f"/work-items/{wid}/pause", json={}).status_code == 409

        assert client.post(f"/work-items/{wid}/steer", json={"text": "  "}).status_code == 400
        assert (
            client.post(
                f"/work-items/{wid}/steer", json={"text": "keep the old signature"}
            ).status_code
            == 200
        )

        # this time let the agent finish
        monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "fix")
        r = client.post(f"/work-items/{wid}/resume", json={})
        assert r.status_code == 200
        assert r.json()["steer"] == "keep the old signature"

        _wait(
            lambda: any(
                e["type"] == "work_item_completed"
                for e in client.get(f"/work-items/{wid}/events").json()
            ),
            "the resumed item to complete",
            timeout=120,
        )

        # a fresh session ran the same hook, and the steer led its prompt exactly once
        rows = client.get(f"/work-items/{wid}").json()["worker_sessions"]
        impl = [s for s in rows if s["hook_point"] == "on.implementation.start"]
        assert len(impl) == 2
        sent = [p for p in prompts.read_text().split("\n\x00\n") if p.strip()]
        steered = [p for p in sent if "keep the old signature" in p]
        assert len(steered) == 1
        assert steered[0].startswith("A human has steered this run:")

        # the steer is spent, and the counters were never touched
        assert client.get(f"/work-items/{wid}").json()["pending_steer_context"] is None
        types = [e["type"] for e in client.get(f"/work-items/{wid}/events").json()]
        assert "steer_context_set" in types and "work_item_resumed" in types


def test_steer_and_resume_are_refused_while_the_item_is_running(tmp_path, monkeypatch):
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE", "slow")
    monkeypatch.setenv("KRAFT_FAKE_CLAUDE_DELAY", "10")
    repo = make_repo(tmp_path)
    with _client(tmp_path, monkeypatch) as client:
        wid = client.post(
            "/work-items",
            json={"repo": str(repo), "title": "busy", "chain_template": "quick-task"},
        ).json()["id"]
        _running_agent(client, wid)
        assert client.post(f"/work-items/{wid}/steer", json={"text": "x"}).status_code == 409
        assert client.post(f"/work-items/{wid}/resume", json={}).status_code == 409
        assert client.post("/work-items/nope/pause", json={}).status_code == 404
        client.post(f"/work-items/{wid}/pause", json={})


def test_a_paused_row_and_its_event_never_disagree(tmp_path):
    """reattach calls session_exited('failed') on a child that dies after a
    restart. If only the row were guarded, the event would still say 'failed' and
    the UI would follow the event."""
    import asyncio

    from kraft import db, events, store

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
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="l",
                    result_path="r",
                )
            )
            await database.write(lambda c: store.pause_work_item(c, "w", ["s1"]))
            # the SIGTERMed child's exit arrives afterwards
            await database.write(lambda c: store.session_exited(c, "s1", "failed"))
            row = database.read(
                lambda c: c.execute("SELECT status FROM worker_sessions WHERE id='s1'").fetchone()
            )
            types = [e["type"] for e in database.read(lambda c: events.read_after(c, 0, "w"))]
            return row["status"], types
        finally:
            await database.close()

    status, types = asyncio.run(scenario())
    assert status == "paused"
    assert "worker_session_exited" not in types


def test_a_pause_catches_a_session_still_in_its_pending_window(tmp_path):
    """A row is inserted before Popen returns. A pause landing in that window has
    to mark it, and the launch finishing must not undo the mark."""
    import asyncio

    from kraft import db, store

    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, current_node_id, created_at, updated_at) "
                    "VALUES ('w','t','/r','quick-task','{}','active','verify','now','now')"
                )
            )
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s1",
                    work_item_id="w",
                    node_id="verify",
                    hook_point="on.test.run",
                    log_path="l",
                    result_path="r",
                )
            )
            pending = database.read(lambda c: store.running_sessions_for_node(c, "w"))
            await database.write(
                lambda c: store.pause_work_item(c, "w", [r["id"] for r in pending])
            )
            # the launch completes a moment later
            await database.write(lambda c: store.session_running(c, "s1", 999, 1.0))
            return [r["id"] for r in pending], database.read(
                lambda c: store.session_status(c, "s1")
            )
        finally:
            await database.close()

    caught, status = asyncio.run(scenario())
    assert caught == ["s1"]
    assert status == "paused"
