"""Shared test seams for the split `test_api_*.py` files: a TestClient wired
to a hermetic env, and the polling helpers that wait on the async executor.
"""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from fastapi.testclient import TestClient

from support.harness import fake_templates_dir, isolated_bd

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch, *, templates_dir=None, peer=("127.0.0.1", 54321)):
    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv(
        "KRAFT_TEMPLATES_DIR",
        str(templates_dir or fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))),
    )
    # Hermetic against a real frontend/dist appearing (3B `npm run build`);
    # a test that already pinned its own dist keeps it.
    monkeypatch.setenv(
        "KRAFT_FRONTEND_DIST", os.environ.get("KRAFT_FRONTEND_DIST") or str(tmp_path / "no-dist")
    )
    import kraft.api as api

    return TestClient(api.app, client=peer)


def _poll_events(client, wid, want, timeout=30, count=1):
    """Wait until `want` has been appended at least `count` times."""
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        seen = client.get(f"/api/work-items/{wid}/events").json()
        if sum(e["type"] == want for e in seen) >= count:
            return seen
        time.sleep(0.2)
    raise AssertionError(f"{want} x{count} not seen; got {[e['type'] for e in seen]}")


def _await_gate(client, wid, gate, timeout=30):
    """Wait for the server to report `gate` as the one waiting on a person."""
    deadline = time.monotonic() + timeout
    item = {}
    while time.monotonic() < deadline:
        item = client.get(f"/api/work-items/{wid}").json()
        if item.get("pending_gate") == gate:
            return item
        time.sleep(0.2)
    raise AssertionError(f"{gate} never became pending; item={item.get('pending_gate')!r}")


def _wait_for_status(client, wid, status, timeout=30):
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/work-items/{wid}").json()
        if body["status"] == status:
            return body
        time.sleep(0.15)
    raise AssertionError(f"status never became {status!r}; last body={body}")


def _post_default(client, repo):
    return client.post(
        "/api/work-items",
        json={
            "title": "make the failing test pass",
            "repo": str(repo),
            "chain_template": "default",
        },
    ).json()["id"]


def _set_status(wid: str, status: str) -> None:
    """Force a work item's status. The states this test needs — one item wedged
    active while another waits paused — are transient under real orchestration,
    so they are written directly rather than raced for."""
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute("UPDATE work_items SET status = ? WHERE id = ?", (status, wid))
        conn.commit()
    finally:
        conn.close()


def _force_node(wid: str, node_id: str, status: str) -> None:
    """Force a work item onto a given node and status, without walking the
    chain to get there for real (Kraft-bz9b's repro needs a task failure at
    `open_mr` specifically, which the test registry's noop binding never
    produces on its own)."""
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    try:
        conn.execute(
            "UPDATE work_items SET status = ?, current_node_id = ? WHERE id = ?",
            (status, node_id, wid),
        )
        conn.commit()
    finally:
        conn.close()
