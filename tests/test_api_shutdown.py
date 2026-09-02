from __future__ import annotations

import signal
import sqlite3
import time
from pathlib import Path

import pytest
from support.harness import fake_templates_dir, isolated_bd, make_repo
from support.server import running_server

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


@pytest.mark.slow
def test_sigterm_shuts_down_cleanly_mid_task(tmp_path):
    """SIGTERM while an executor task is running must not hang and must leave the DB usable."""
    run_dir = tmp_path / "run"
    templates = fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))
    tracker = isolated_bd(tmp_path)
    repo = make_repo(tmp_path)
    slow_env = {"KRAFT_FAKE_CLAUDE": "slow", "KRAFT_FAKE_CLAUDE_DELAY": "15"}

    with running_server(
        run_dir=run_dir, templates_dir=templates, bd_cwd=tracker, env=slow_env
    ) as srv:
        wid = srv.client.post(
            "/work-items", json={"title": "make the failing test pass", "repo": str(repo)}
        ).json()["id"]
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            types = [e["type"] for e in srv.client.get(f"/work-items/{wid}/events").json()]
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
