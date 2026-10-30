"""Approving a one-off chain's custom content, and what never starts without it."""

from __future__ import annotations

import sqlite3

import pytest
from support.api import CUSTOM_ONE_OFF as CUSTOM
from support.api import _one_off, _orchestrator_db

from kraft import store

MCP = {"X-Kraft-Client": "mcp"}
WORKER = {"X-Kraft-Session-Id": "other/abc123"}
LIBRARY_ONLY = "extends: default\nremove: [work_brief]\n"
EDIT_PROMPT = {
    "op": "override",
    "path": "implementation.main.implement",
    "task_config": {"prompt": "do something else"},
}


def _review(client, wid) -> dict:
    r = client.get(f"/api/work-items/{wid}/one-off")
    assert r.status_code == 200, r.text
    return r.json()


def _approve(client, wid, digest=None, headers=None, **body):
    return client.post(
        f"/api/work-items/{wid}/one-off/approve",
        json={"digest": digest or _review(client, wid)["digest"], **body},
        headers=headers or {},
    )


def _resume(client, wid, headers=None):
    return client.post(f"/api/work-items/{wid}/resume", json={}, headers=headers or {})


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="browser"),
        pytest.param(MCP, id="mcp"),
        pytest.param(WORKER, id="worker"),
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
    wid = _one_off(client, repo, LIBRARY_ONLY)
    assert _review(client, wid)["needs_review"] is False
    assert _resume(client, wid).status_code == 200


@pytest.mark.parametrize(
    ("headers", "text", "digest", "status"),
    [
        pytest.param(MCP, CUSTOM, None, 403, id="mcp"),
        pytest.param({}, LIBRARY_ONLY, None, 409, id="library-only"),
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
