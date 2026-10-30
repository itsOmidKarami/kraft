"""Approving a one-off chain's custom content, and what never starts without it."""

from __future__ import annotations

import sqlite3

import pytest
from support.api import CUSTOM_ONE_OFF as CUSTOM
from support.api import (
    LIBRARY_ONLY_ONE_OFF,
    MCP_HEADERS,
    WORKER_HEADERS,
    _approve,
    _come_after,
    _item_row,
    _one_off,
    _orchestrator_db,
    _queue_tick,
    _review,
    _set_status,
)

from kraft import store

EDIT_PROMPT = {
    "op": "override",
    "path": "implementation.main.implement",
    "task_config": {"prompt": "do something else"},
}


def _resume(client, wid, headers=None):
    return client.post(f"/api/work-items/{wid}/resume", json={}, headers=headers or {})


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="browser"),
        pytest.param(MCP_HEADERS, id="mcp"),
        pytest.param(WORKER_HEADERS, id="worker"),
    ],
)
def test_resume_refuses_unapproved_custom(client, repo, headers):
    wid = _one_off(client, repo, CUSTOM)
    r = _resume(client, wid, headers)
    assert r.status_code == 409, r.text
    assert "no one has approved" in r.json()["detail"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "paused"


def test_claim_for_run_skips_pending(client, repo):
    wid = _one_off(client, repo, CUSTOM)
    with _orchestrator_db() as conn:
        conn.row_factory = sqlite3.Row
        assert store.claim_for_run(conn, wid, from_statuses=["paused"]) is False
        assert (
            conn.execute("SELECT status FROM work_items WHERE id = ?", (wid,)).fetchone()[0]
            == "paused"
        )


def test_resume_starts_after_approval(client, repo):
    wid = _one_off(client, repo, CUSTOM)
    assert _approve(client, wid).status_code == 200
    assert _review(client, wid)["needs_review"] is False
    assert _resume(client, wid).status_code == 200


def test_library_only_one_off_starts_without_review(client, repo):
    wid = _one_off(client, repo, LIBRARY_ONLY_ONE_OFF)
    assert _review(client, wid)["needs_review"] is False
    assert _resume(client, wid).status_code == 200


@pytest.mark.parametrize(
    ("headers", "text", "digest", "status"),
    [
        pytest.param(MCP_HEADERS, CUSTOM, None, 403, id="mcp"),
        pytest.param({}, LIBRARY_ONLY_ONE_OFF, None, 409, id="library-only"),
        pytest.param({}, CUSTOM, "0" * 64, 409, id="stale-digest"),
        pytest.param({}, CUSTOM, None, 200, id="ok"),
    ],
)
def test_approve(client, repo, headers, text, digest, status):
    wid = _one_off(client, repo, text)
    r = _approve(client, wid, digest, headers)
    assert r.status_code == status, r.text
    assert (_review(client, wid)["approval"] is not None) is (status == 200)


def test_approve_records_the_door(client, repo):
    wid = _one_off(client, repo, CUSTOM)
    approval = _approve(client, wid, door="phone").json()["approval"]
    assert (approval["by"], approval["door"]) == ("human", "phone")
    assert _review(client, wid)["approval"] == approval


def test_approve_refuses_a_library_item_and_a_started_one(client, repo):
    plain = client.post("/api/work-items", json={"title": "t", "repo": str(repo)}).json()["id"]
    assert client.get(f"/api/work-items/{plain}/one-off").status_code == 404
    assert _approve(client, plain, "0" * 64).status_code == 404
    wid = _one_off(client, repo, CUSTOM)
    digest = _review(client, wid)["digest"]
    with _orchestrator_db() as conn, conn:
        conn.execute("UPDATE work_items SET current_node_id = 'spec' WHERE id = ?", (wid,))
    assert _approve(client, wid, digest).status_code == 409


@pytest.mark.parametrize(
    "change",
    [
        pytest.param("draft", id="draft-after-approval"),
        pytest.param("attachment", id="attachment-retrim"),
    ],
)
def test_change_reopens_review(client, repo, change):
    wid = _one_off(client, repo, CUSTOM)
    assert _approve(client, wid).status_code == 200
    if change == "draft":
        client.put(f"/api/work-items/{wid}/draft", json={"ops": [EDIT_PROMPT]}).raise_for_status()
        client.post(f"/api/work-items/{wid}/draft/apply").raise_for_status()
    else:
        (repo / "spec.md").write_text("# spec\n")
        r = client.patch(f"/api/work-items/{wid}", json={"attachments": {"spec": "spec.md"}})
        assert r.status_code == 200, r.text
    assert _review(client, wid)["needs_review"] is True
    assert _review(client, wid)["approval"] is None
    assert _resume(client, wid).status_code == 409


def test_the_walk_stops_an_item_whose_chain_changed_after_the_claim(client, repo):
    """The walk itself, not `load_chain` alone: an item claimed `active` whose chain then
    changed stops `needs_human` before its first node, runs nothing, and starts again once
    a person approves the new chain."""
    from functools import partial

    from kraft.executor import walk

    wid = _one_off(client, repo, CUSTOM)
    assert _approve(client, wid).status_code == 200
    _set_status(wid, "active")
    (repo / "spec.md").write_text("# spec\n")
    r = client.patch(f"/api/work-items/{wid}", json={"attachments": {"spec": "spec.md"}})
    assert r.status_code == 200, r.text
    st = client.app.state
    done = client.portal.call(partial(walk.run_once, st.db, st.run_dirs, work_item_id=wid))
    row = _item_row(wid)
    assert done == "needs_human"
    assert (row["status"], row["stop_kind"], row["current_node_id"]) == (
        "needs_human",
        "config",
        None,
    )
    assert client.get(f"/api/work-items/{wid}").json()["worker_sessions"] == []
    assert _approve(client, wid).status_code == 200
    assert _resume(client, wid).status_code == 200


def test_approve_refuses_a_chain_changed_since_the_review(client, repo):
    """[draft-before-approve] The digest a person approves covers the chain that will
    run, so a draft applied after they opened the review cannot ride on their approval."""
    wid = _one_off(client, repo, CUSTOM)
    shown = _review(client, wid)["digest"]
    client.put(f"/api/work-items/{wid}/draft", json={"ops": [EDIT_PROMPT]}).raise_for_status()
    client.post(f"/api/work-items/{wid}/draft/apply").raise_for_status()
    assert _approve(client, wid, shown).status_code == 409
    assert _review(client, wid)["approval"] is None
    assert _approve(client, wid).status_code == 200


def test_a_change_after_the_claim_stops_the_walk_before_it_loads(client, repo):
    """The item was claimed `active` and then its chain changed: the first
    `load_chain` refuses, so nothing unreviewed runs, and the review is open again."""
    wid = _one_off(client, repo, CUSTOM)
    assert _approve(client, wid).status_code == 200
    _set_status(wid, "active")
    (repo / "spec.md").write_text("# spec\n")
    r = client.patch(f"/api/work-items/{wid}", json={"attachments": {"spec": "spec.md"}})
    assert r.status_code == 200, r.text
    assert _item_row(wid)["one_off_pending"] == 1
    with _orchestrator_db() as conn, conn:
        conn.row_factory = sqlite3.Row
        assert store.load_chain(conn, wid, "spec") is False
    assert _item_row(wid)["current_node_id"] is None


def test_review_shows_filed_provenance(client, repo, templates_dir):
    text = (
        "extends: quick-task\nadd:\n  - after: verify\n    node:\n"
        "      id: k6\n      kind: exec\n      tasks:\n"
        "        - {id: run, kind: agent, harness: claude, prompt: run k6}\n"
    )
    wid = _one_off(client, repo, text)
    before = _review(client, wid)["provenance"]
    path = templates_dir / "chains" / "quick-task.yaml"
    path.write_text(path.read_text().replace("id: verify\n", "id: verify_later\n", 1))
    client.post("/api/templates/reload")
    review = _review(client, wid)
    assert review["provenance"] == before
    assert [a["node"] for a in review["added"]] == ["k6"]


def test_start_queue_refuses_unapproved(client, repo):
    dep = client.post("/api/work-items", json={"title": "dep", "repo": str(repo)}).json()["id"]
    wid = _one_off(client, repo, CUSTOM)
    assert _approve(client, wid).status_code == 200
    _come_after(wid, [dep])
    assert _resume(client, wid).json()["status"] == "blocked"
    other = CUSTOM.replace("run k6", "run k7")
    r = client.patch(f"/api/work-items/{wid}", json={"one_off_chain": other})
    assert r.status_code == 200, r.text
    assert _item_row(wid)["current_node_id"] is None
    _set_status(dep, "completed")
    _queue_tick(client)
    events = client.get(f"/api/work-items/{wid}/events").json()
    [event] = [e for e in events if e["type"] == "work_item_dequeued"]
    assert event["payload"]["why"] == "refused"
    assert "no one has approved" in event["payload"]["detail"]
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] != "active" and item["worker_sessions"] == []


def test_a_queued_start_on_a_pending_item_is_refused(client, repo):
    from functools import partial

    wid = _one_off(client, repo, CUSTOM)
    st = client.app.state
    queued = client.portal.call(
        partial(
            st.db.write,
            lambda c: store.queue_work_item(
                c, wid, verb="start", body={}, headers={}, from_statuses=["paused"]
            ),
        )
    )
    assert queued is True
    _queue_tick(client)
    events = client.get(f"/api/work-items/{wid}/events").json()
    [event] = [e for e in events if e["type"] == "work_item_dequeued"]
    assert event["payload"]["why"] == "refused"
    assert "no one has approved" in event["payload"]["detail"]
    assert _item_row(wid)["status"] != "active"


def test_retry_refuses_a_pending_item(client, repo):
    wid = _one_off(client, repo, CUSTOM)
    r = client.post(f"/api/work-items/{wid}/retry", json={})
    assert r.status_code == 409 and "no one has approved" in r.json()["detail"]
