"""A skip supersedes the step outputs of the writers it skips."""

from __future__ import annotations

import pytest
from support.api import _force_node
from support.harness import v1_chain, v1_node, v1_task

from kraft import executor, item_paths, store
from kraft.api.routes import lifecycle

NODE = "build"
FIRST, SECOND = f"{NODE}.first.w", f"{NODE}.second.w"


def _writer(id: str) -> dict:
    return v1_task(id, outputs={"files": ["a.txt"]})


def _snapshot(client, wid: str, writer: str):
    return item_paths.snapshot_dir(client.app.state.run_dirs, wid, writer)


@pytest.fixture
def walked(monkeypatch):
    async def fake(*args, **kwargs):
        return "completed"

    monkeypatch.setattr(executor, "run", fake)


def _item_with_two_writers(client, repo, status: str) -> str:
    """An item on node `build`, whose steps `first` and `second` each hold a
    writer, each with a snapshot already kept."""
    wid = client.post(
        "/api/work-items",
        json={"title": "t", "repo": str(repo), "chain_template": "default", "autostart": False},
    ).json()["id"]
    chain = v1_chain(
        [
            v1_node(
                NODE,
                steps=[
                    {"id": "first", "tasks": [_writer("w")]},
                    {"id": "second", "tasks": [_writer("w")]},
                ],
            )
        ],
        repo=repo,
    )
    db = client.app.state.db
    client.portal.call(
        lambda: db.write(
            lambda c: c.execute(
                "UPDATE work_items SET materialized_chain = ? WHERE id = ?", (chain.to_json(), wid)
            )
        )
    )
    _force_node(wid, NODE, status)
    for writer in (FIRST, SECOND):
        folder = _snapshot(client, wid, writer)
        folder.mkdir(parents=True)
        (folder / "a.txt").write_text("kept from the last run")
    return wid


def _run_session(client, wid: str, path: str) -> None:
    db = client.app.state.db

    async def start():
        await db.write(
            lambda c: store.create_session(
                c,
                id="s-run",
                work_item_id=wid,
                node_id=NODE,
                hook_point=path,
                log_path="/l",
                result_path="/r",
            )
        )
        await db.write(lambda c: store.session_running(c, "s-run", 111, 1.0, work_item_id=wid))

    client.portal.call(start)


@pytest.mark.parametrize(
    ("path", "status", "running", "cleared"),
    [
        (FIRST, "needs_human", False, [FIRST]),
        (FIRST, "active", True, [FIRST]),
        (f"{NODE}.first", "needs_human", False, [FIRST]),
        (None, "needs_human", False, [FIRST, SECOND]),
    ],
    ids=["task-before-run", "task-while-running", "step", "node"],
)
def test_skip_clears_snapshot(client, repo, walked, monkeypatch, path, status, running, cleared):
    monkeypatch.setattr(lifecycle, "_terminate", lambda *a, **k: None)
    wid = _item_with_two_writers(client, repo, status)
    if running:
        _run_session(client, wid, FIRST)

    r = client.post(f"/api/work-items/{wid}/skip", json={"path": path} if path else {})

    assert r.status_code == 200, r.text
    for writer in (FIRST, SECOND):
        assert _snapshot(client, wid, writer).exists() is (writer not in cleared)
