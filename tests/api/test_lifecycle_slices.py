"""The doors at a slice group that has forked and not joined: what a parent
refuses whatever its status, and what pausing, resuming and ending it do to its
children."""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from functools import partial
from pathlib import Path

import pytest
from support.api import _paused, _status_of

from kraft import store
from kraft.executor import slices


def _db():
    return closing(sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db"))


def _family(client, repo, *, parent_status="waiting", children=("running", "running")):
    """A parent at a forked group and one child per status, written directly."""
    parent = _paused(client, repo, chain_template="default")
    kids = [_paused(client, repo, chain_template="default") for _ in children]
    state = store.GroupState(group="slices", phase="forked", generation=1)
    with _db() as c, c:
        c.execute(
            "UPDATE work_items SET status = ?, current_node_id = 'implementation', "
            "stop_kind = ?, group_state = ? WHERE id = ?",
            (
                parent_status,
                "slices" if parent_status == "waiting" else None,
                state.to_json(),
                parent,
            ),
        )
        for key, (kid, status) in enumerate(zip(kids, children, strict=True), 1):
            c.execute(
                "UPDATE work_items SET parent_id = ?, slice_generation = 1, slice_key = ?, "
                "status = ?, current_node_id = 'implementation' WHERE id = ?",
                (parent, key, {"running": "active"}.get(status, status), kid),
            )
    return parent, kids


@pytest.mark.parametrize("status", ["waiting", "paused", "needs_human"])
@pytest.mark.parametrize(
    ("verb", "path", "body"),
    [
        pytest.param("skip", "skip", {}, id="skip"),
        pytest.param("complete", "complete", {"reason": "by hand"}, id="complete"),
    ],
)
def test_an_unjoined_parent_refuses(client, repo, status, verb, path, body):
    parent, kids = _family(client, repo, parent_status=status)

    r = client.post(f"/api/work-items/{parent}/{path}", json=body)

    assert r.status_code == 409, r.text
    assert kids[0] in r.json()["detail"]
    assert _status_of(client, parent) == status


def test_an_unjoined_parent_refuses_a_retry_from_an_earlier_node(client, repo):
    parent, kids = _family(client, repo, parent_status="needs_human")
    with _db() as c, c:
        c.execute("UPDATE work_items SET current_node_id = 'verification' WHERE id = ?", (parent,))

    r = client.post(f"/api/work-items/{parent}/retry", json={"path": "plan"})

    assert r.status_code == 409, r.text
    assert "unjoined" in r.json()["detail"]


def test_pause_and_resume_cascade_to_the_children_the_parent_paused(client, repo):
    parent, kids = _family(client, repo, parent_status="active", children=("running", "paused"))
    with _db() as c, c:
        c.execute("UPDATE work_items SET stop_kind = NULL WHERE id = ?", (parent,))

    assert client.post(f"/api/work-items/{parent}/pause").status_code == 200

    assert [_status_of(client, k) for k in kids] == ["paused", "paused"]
    with _db() as c:
        flags = [
            c.execute("SELECT paused_by_parent FROM work_items WHERE id=?", (k,)).fetchone()[0]
            for k in kids
        ]
    assert flags == [1, 0]  # the already-paused one stays a person's

    woken = client.portal.call(partial(slices.resume_family, client.app.state.db, parent))
    assert woken == [kids[0]]
    assert _status_of(client, kids[0]) == "queued"
    assert _status_of(client, kids[1]) == "paused"


def test_abandon_pauses_then_abandons_every_child(client, repo):
    parent, kids = _family(
        client, repo, parent_status="paused", children=("running", "needs_human")
    )

    r = client.post(f"/api/work-items/{parent}/abandon", json={"reason": "drop it"})

    assert r.status_code == 200, r.text
    assert [_status_of(client, k) for k in kids] == ["abandoned", "abandoned"]
    assert _status_of(client, parent) == "abandoned"


def test_cancel_keeps_the_childrens_worktrees(client, repo):
    parent, kids = _family(client, repo, parent_status="paused", children=("needs_human", "paused"))

    r = client.post(f"/api/work-items/{parent}/cancel", json={"reason": "stop"})

    assert r.status_code == 200, r.text
    assert [_status_of(client, k) for k in kids] == ["abandoned", "abandoned"]
    events = client.get(f"/api/work-items/{kids[0]}/events").json()
    assert "work_item_cancelled" in [e["type"] for e in events]


def test_a_child_cannot_be_archived_alone(client, repo):
    parent, kids = _family(client, repo, children=("completed", "completed"))

    r = client.post(f"/api/work-items/{kids[0]}/archive")

    assert r.status_code == 409, r.text
    assert parent in r.json()["detail"]


@pytest.mark.parametrize("acts_on", ["parent", "sibling"])
def test_a_worker_cannot_act_on_its_own_family(client, repo, acts_on):
    """A worker of one slice does not pause its parent or a sibling either."""
    parent, kids = _family(client, repo, parent_status="paused", children=("paused", "paused"))
    with _db() as c, c:
        store.create_session(
            c,
            id="s1",
            work_item_id=kids[0],
            node_id="implementation",
            hook_point="implementation.main.x",
            log_path="/tmp/kraft-test.log",
            result_path="/tmp/kraft-test.json",
        )
    target = parent if acts_on == "parent" else kids[1]

    r = client.post(
        f"/api/work-items/{target}/pause", headers={"X-Kraft-Session-Id": f"{kids[0]}/s1"}
    )

    assert r.status_code == 403, r.text
