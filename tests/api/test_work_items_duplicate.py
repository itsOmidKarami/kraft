"""Duplicating a work item (B3). A sibling of test_work_items.py."""

from __future__ import annotations

from pathlib import Path

from support.api import _poll_events, _post_default, _set_status
from support.harness import make_repo_with_engineering

# --- B3: duplicate ----------------------------------------------------------


def test_duplicate_carries_the_listed_fields_and_nothing_else(client, tmp_path):
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "description": "d",
            "repo": str(repo),
            "chain_template": "default",
            "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
            "budget_usd": 5.0,
            "autostart": False,
        },
    )
    assert r.status_code == 201, r.text
    src_id = r.json()["id"]
    src = client.get(f"/api/work-items/{src_id}").json()

    dr = client.post(f"/api/work-items/{src_id}/duplicate")
    assert dr.status_code == 201, dr.text
    assert dr.json()["status"] == "paused"
    new_id = dr.json()["id"]
    assert new_id != src_id

    dup = client.get(f"/api/work-items/{new_id}").json()
    assert dup["title"] == src["title"]
    assert dup["description"] == src["description"]
    assert dup["repo"] == src["repo"]
    assert dup["chain_template"] == src["chain_template"]
    assert dup["status"] == "paused"
    # No run state and no budget carries over: the source's own explicit cap
    # is not the new item's.
    assert dup["budget_cap"]["cap_usd"] != 5.0

    expected = [{"kind": "plan", "path": ".engineering/plans/p.md"}]
    assert [{k: v for k, v in a.items() if k != "source"} for a in dup["attachments"]] == expected
    # Its own stored copy, not the source's -- re-snapshotted under the new id.
    assert dup["attachments"][0]["source"] != src["attachments"][0]["source"]
    assert Path(dup["attachments"][0]["source"]).is_relative_to(
        client.app.state.run_dirs.attachments / new_id
    )


def test_duplicate_works_from_a_cancelled_source(client, repo):
    src_id = _post_default(client, repo)
    _poll_events(client, src_id, "gate_requested")
    _set_status(src_id, "active")
    cr = client.post(f"/api/work-items/{src_id}/cancel", json={"reason": "no longer needed"})
    assert cr.status_code == 200, cr.text

    dr = client.post(f"/api/work-items/{src_id}/duplicate")

    assert dr.status_code == 201, dr.text
    assert dr.json()["status"] == "paused"


def test_duplicate_works_from_an_archived_source(client, tmp_path):
    """Archive keeps the attachment copies, so an archived item with a plan
    duplicates, plan and all."""
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "autostart": False,
            "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
        },
    )
    assert r.status_code == 201, r.text
    src_id = r.json()["id"]
    client.post(f"/api/work-items/{src_id}/cancel", json={"reason": "no longer needed"})
    ar = client.post(f"/api/work-items/{src_id}/archive")
    assert ar.status_code == 200, ar.text

    dr = client.post(f"/api/work-items/{src_id}/duplicate")

    assert dr.status_code == 201, dr.text
    assert dr.json()["status"] == "paused"
    dup = client.get(f"/api/work-items/{dr.json()['id']}").json()
    assert Path(dup["attachments"][0]["source"]).read_text() == "# plan\n"


def test_duplicate_with_a_missing_stored_attachment_answers_409(client, tmp_path):
    repo = make_repo_with_engineering(tmp_path, {".engineering/plans/p.md": "# plan\n"})
    r = client.post(
        "/api/work-items",
        json={
            "title": "t",
            "repo": str(repo),
            "autostart": False,
            "attachments": [{"kind": "plan", "path": ".engineering/plans/p.md"}],
        },
    )
    assert r.status_code == 201, r.text
    src_id = r.json()["id"]
    src = client.get(f"/api/work-items/{src_id}").json()
    Path(src["attachments"][0]["source"]).unlink()

    dr = client.post(f"/api/work-items/{src_id}/duplicate")

    assert dr.status_code == 409, dr.text
    detail = dr.json()["detail"]
    assert "no longer has this item's plan" in detail
    assert "attach the plan again" in detail
