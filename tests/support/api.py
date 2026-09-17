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
    from kraft.api import deps

    # `setup_command` has no default (Kraft-kji8w): a repo this suite never
    # connected through `POST /repos` gets no entry at all, and dispatch would
    # stop every one of these tests at `needs_human` before they reach the
    # behavior they actually test. Most of this file is not about repo
    # config -- the handful that are (test_api_repos.py) drive `load_repos`
    # directly rather than through this fixture.
    real_connected = deps._connected

    def _connected_or_default(repos, path):
        return real_connected(repos, path) or {"setup_command": ""}

    monkeypatch.setattr(deps, "_connected", _connected_or_default)

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


def _poll_node_started(client, wid, node_id, timeout=30):
    """Like `_poll_events(..., "node_started")`, but waits for *this* node's
    own start, not just any `node_started` -- a plain type/count check can
    already be satisfied by an earlier node before the gate approval or skip
    that this call follows has actually resumed the walk, since `approve_gate`/
    `skip_node` write their own event synchronously before the continuation
    is spawned in the background (Kraft-tsfpk added one more `await` -- a
    `bd blocked` check -- ahead of every dispatch, widening that race enough
    to make it flake for real)."""
    deadline = time.monotonic() + timeout
    seen = []
    while time.monotonic() < deadline:
        seen = client.get(f"/api/work-items/{wid}/events").json()
        if any(e["type"] == "node_started" and e["payload"]["node_id"] == node_id for e in seen):
            return seen
        time.sleep(0.2)
    raise AssertionError(f"node_started for {node_id!r} not seen; got {[e['type'] for e in seen]}")


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


def _approve_gate(client, wid, gate, timeout=30):
    """Approve a gate, retrying past a 409.

    An `auto_escalate` node's own walk (chain_review, in the shipped `default`
    template) can still be inside its own auto-review agent call when this
    gate first becomes pending -- `deps.spawn`'s `AlreadyRunning` refusal
    (Kraft-11e0) then 409s a manual approve that lands in that window. Retry
    until the in-flight review finishes and frees the task slot, rather than
    every caller re-deriving this.
    """
    deadline = time.monotonic() + timeout
    r = None
    while time.monotonic() < deadline:
        r = client.post(f"/api/work-items/{wid}/gates/{gate}/approve")
        if r.status_code != 409:
            return r
        time.sleep(0.2)
    raise AssertionError(f"{gate} still 409 after {timeout}s: {r.text if r else '(no attempt)'}")


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
