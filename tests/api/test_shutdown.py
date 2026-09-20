from __future__ import annotations

import signal
import sqlite3
import time
from pathlib import Path

import pytest
import yaml
from support.harness import fake_templates_dir, isolated_bd, make_repo
from support.server import running_server

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


@pytest.mark.slow
def test_sigterm_shuts_down_cleanly_mid_task(tmp_path):
    """SIGTERM while an executor task is running must not hang and must leave the DB usable."""
    run_dir = tmp_path / "run"
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    # A worktree needs a declared setup_command since Kraft-kji8w; this test
    # is about shutdown, not preparation, so declare deliberately nothing else.
    # `env_passthrough` is what actually gets `KRAFT_FAKE_CLAUDE*` from this
    # real daemon subprocess's own environment into the worker it launches --
    # the autouse fixture in conftest.py that does this for in-process tests
    # has no effect here, since the daemon runs as a separate `python -m kraft`.
    (templates / "repos.yaml").write_text(
        yaml.safe_dump(
            {
                "repos": [
                    {
                        "path": str(repo),
                        "setup_command": "",
                        "env_passthrough": ["KRAFT_FAKE_CLAUDE", "KRAFT_FAKE_CLAUDE_DELAY"],
                    }
                ]
            }
        )
    )
    slow_env = {"KRAFT_FAKE_CLAUDE": "slow", "KRAFT_FAKE_CLAUDE_DELAY": "15"}

    with running_server(
        run_dir=run_dir, templates_dir=templates, bd_cwd=tracker, env=slow_env
    ) as srv:
        wid = srv.client.post(
            "/api/work-items",
            # quick-task, not the default chain: this test needs an agent
            # session running within 20s, and `default` stops at spec_approval.
            json={
                "title": "make the failing test pass",
                "repo": str(repo),
                "chain_template": "quick-task",
            },
        ).json()["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            types = [e["type"] for e in srv.client.get(f"/api/work-items/{wid}/events").json()]
            if "worker_session_started" in types:
                break
            time.sleep(0.2)
        else:
            raise AssertionError("no session started before shutdown")

        srv.proc.terminate()
        srv.proc.wait(timeout=10)
        # uvicorn exits via the signal (-SIGTERM) after running lifespan shutdown;
        # what matters is that .wait() returned quickly (no hang) — see the timeout above.
        assert srv.proc.returncode in (0, -signal.SIGTERM)

    conn = sqlite3.connect(run_dir / "orchestrator.db")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()
        assert row["status"] in ("active", "needs_human", "completed")
    finally:
        conn.close()


@pytest.mark.slow
def test_sigterm_exits_with_a_websocket_client_connected(tmp_path):
    """A human with the board open holds `/ws/events` open.

    Uvicorn's graceful shutdown waits for every open connection, and asks each
    WebSocket to close by delivering a `websocket.disconnect` to `receive()`. A
    handler that only ever sends never learns it was asked, so the server sat
    there and `kraft admin stop` reported a failure that was real (Kraft-9oab).
    """
    from websockets.sync.client import connect

    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)
    with running_server(run_dir=tmp_path / "run", templates_dir=templates, bd_cwd=tracker) as srv:
        with connect(f"ws://127.0.0.1:{srv.port}/api/ws/events?after_seq=0"):
            srv.proc.terminate()
            srv.proc.wait(timeout=10)
        assert srv.proc.returncode in (0, -signal.SIGTERM)
