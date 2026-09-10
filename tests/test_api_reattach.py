from __future__ import annotations

import time
from pathlib import Path

import psutil
import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo
from support.server import running_server

from kraft import db as kdb
from kraft import store

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _poll(client, wid, want, timeout=30, pred=None):
    deadline = time.monotonic() + timeout
    types = []
    while time.monotonic() < deadline:
        evs = client.get(f"/api/work-items/{wid}/events").json()
        types = [e["type"] for e in evs]
        if any(e["type"] == want and (pred is None or pred(e)) for e in evs):
            return evs
        time.sleep(0.2)
    raise AssertionError(f"{want} not seen; got {types}")


def _impl(e):
    return e["payload"].get("hook_point") == "on.implementation.start"


@pytest.mark.slow
def test_reattach_adopts_running_agent(tmp_path):
    run_dir = tmp_path / "run"
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    slow_env = {"KRAFT_FAKE_CLAUDE": "slow", "KRAFT_FAKE_CLAUDE_DELAY": "8"}

    with running_server(
        run_dir=run_dir, templates_dir=templates, bd_cwd=tracker, env=slow_env
    ) as srv:
        wid = srv.client.post(
            "/api/work-items",
            # quick-task, not the default chain: this test needs the
            # implementation agent actually running to kill and re-adopt, and
            # `default` stops at spec_approval before it ever starts.
            json={
                "title": "make the failing test pass",
                "repo": str(repo),
                "chain_template": "quick-task",
            },
        ).json()["id"]
        started = _poll(srv.client, wid, "worker_session_started", pred=_impl)
        pid = next(
            e["payload"]["pid"]
            for e in started
            if e["type"] == "worker_session_started" and _impl(e)
        )
        sess_id = next(
            e["payload"]["session_id"]
            for e in started
            if e["type"] == "worker_session_started" and _impl(e)
        )
        assert psutil.pid_exists(pid)
        srv.kill()

    # the detached fake-claude.sh is still sleeping
    assert psutil.pid_exists(pid)

    with running_server(
        run_dir=run_dir, templates_dir=templates, bd_cwd=tracker, env=slow_env
    ) as srv:
        health = srv.client.get("/api/health").json()
        assert wid in health["reattach_summary"]["resumed_work_items"]
        assert sess_id in health["reattach_summary"]["adopted"], (
            f"expected {sess_id} adopted, got summary {health['reattach_summary']}"
        )
        evs = _poll(srv.client, wid, "session_reattached", timeout=40)
        reattached_pid = next(e["payload"]["pid"] for e in evs if e["type"] == "session_reattached")
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

    chain = json.dumps(
        {
            "template_id": "quick-task",
            "nodes": [
                {"id": "env_setup", "tasks": ["on.env.prepare"], "gate_after": None},
                {"id": "implementation", "tasks": ["on.implementation.start"], "gate_after": None},
                {"id": "verify", "tasks": ["on.test.run"], "gate_after": None},
            ],
        }
    )

    async def seed():
        (run_dir / "logs").mkdir(parents=True, exist_ok=True)
        (run_dir / "results").mkdir(parents=True, exist_ok=True)
        database = await kdb.Database.open(run_dir / "orchestrator.db")
        try:
            await database.write(
                lambda c: store.create_work_item(
                    c,
                    id="w-seed",
                    bead_id="B-1",
                    title="t",
                    repo="/r",
                    chain_template="quick-task",
                    chain_definition=chain,
                )
            )
            await database.write(lambda c: store.load_chain(c, "w-seed", "implementation"))
            await database.write(lambda c: store.enter_node(c, "w-seed", "implementation"))
            await database.write(
                lambda c: store.create_session(
                    c,
                    id="s-seed",
                    work_item_id="w-seed",
                    node_id="implementation",
                    hook_point="on.implementation.start",
                    log_path=str(run_dir / "logs" / "s-seed.log"),
                    result_path=str(run_dir / "results" / "s-seed.json"),
                )
            )
        finally:
            await database.close()

    asyncio.run(seed())

    with running_server(run_dir=run_dir, templates_dir=templates, bd_cwd=tracker) as srv:
        health = srv.client.get("/api/health").json()
        assert "s-seed" in health["reattach_summary"]["unknown"]
        assert srv.client.get("/api/work-items/w-seed").json()["status"] == "needs_human"
