"""Replacing, switching away from and duplicating a one-off chain."""

from __future__ import annotations

import json

import pytest
from support.api import CUSTOM_ONE_OFF as CUSTOM
from support.api import (
    LIBRARY_ONLY_ONE_OFF,
    _approve,
    _item_row,
    _one_off,
    _orchestrator_db,
    _paused,
    _review,
    _set_status,
)

OTHER = CUSTOM.replace("run k6", "run k7")
EXTENDS_QUICK = "extends: quick-task\n"


def _drop_quick_task(templates_dir, client):
    (templates_dir / "chains" / "quick-task.yaml").unlink()
    client.post("/api/templates/reload")


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("library-to-one-off", id="library-to-one-off"),
        pytest.param("drops-approval", id="drops-approval"),
        pytest.param("one-off-to-library", id="one-off-to-library"),
        pytest.param("one-off-to-library-base-gone", id="one-off-to-library-base-gone"),
        pytest.param("started", id="started"),
        pytest.param("needs-human-unstarted", id="needs-human-unstarted"),
        pytest.param("nothing-else", id="nothing-else"),
    ],
)
def test_patch_replaces_one_off(client, repo, templates_dir, case):
    if case == "library-to-one-off":
        wid = _paused(client, repo)
    elif case == "one-off-to-library-base-gone":
        wid = _one_off(client, repo, EXTENDS_QUICK)
        _drop_quick_task(templates_dir, client)
    else:
        wid = _one_off(client, repo, CUSTOM)
    if case == "drops-approval":
        _approve(client, wid).raise_for_status()
        assert _review(client, wid)["needs_review"] is False
    if case == "started":
        with _orchestrator_db() as c, c:
            c.execute("UPDATE work_items SET current_node_id = 'spec' WHERE id = ?", (wid,))
    if case == "needs-human-unstarted":
        _set_status(wid, "needs_human")
    to_library = case.startswith("one-off-to-library")
    body = {"chain": "default"} if to_library else {"one_off_chain": OTHER}
    r = client.patch(f"/api/work-items/{wid}", json=body)
    if case == "started":
        assert r.status_code == 409, r.text
        return
    assert r.status_code == 200, r.text
    row = _item_row(wid)
    if to_library:
        assert row["chain_template"] == "default"
        assert (row["one_off_chain"], row["one_off_approval"], row["one_off_pending"]) == (
            None,
            None,
            0,
        )
        assert client.get(f"/api/work-items/{wid}/one-off").status_code == 404
        return
    assert row["chain_template"] == "@one-off"
    assert "run k7" in row["materialized_chain"]
    assert _review(client, wid)["needs_review"] is True
    assert _review(client, wid)["approval"] is None


def test_patch_with_only_a_one_off_chain_is_something_to_patch(client, repo):
    wid = _one_off(client, repo, LIBRARY_ONLY_ONE_OFF)
    assert client.patch(f"/api/work-items/{wid}", json={"one_off_chain": CUSTOM}).status_code == 200
    assert client.patch(f"/api/work-items/{wid}", json={}).status_code == 422


def test_patch_refuses_two_chains(client, repo):
    wid = _paused(client, repo)
    r = client.patch(f"/api/work-items/{wid}", json={"chain": "default", "one_off_chain": CUSTOM})
    assert r.status_code == 422 and "not both" in json.dumps(r.json())


@pytest.mark.parametrize(
    "text", [pytest.param(CUSTOM, id="custom"), pytest.param(EXTENDS_QUICK, id="base-gone")]
)
def test_duplicate_one_off(client, repo, templates_dir, text):
    wid = _one_off(client, repo, text)
    if text is CUSTOM:
        _approve(client, wid).raise_for_status()
    else:
        _drop_quick_task(templates_dir, client)
    r = client.post(f"/api/work-items/{wid}/duplicate")
    if text is not CUSTOM:
        assert r.status_code == 422 and "one-off chain:" in r.json()["detail"]
        return
    assert r.status_code == 201, r.text
    copy = _item_row(r.json()["id"])
    assert copy["chain_template"] == "@one-off"
    assert json.loads(copy["one_off_chain"])["authored"] == _review(client, wid)["authored"]
    assert (copy["one_off_approval"], copy["one_off_pending"]) == (None, 1)


def test_duplicate_refuses_a_one_off_row_with_no_record(client, repo):
    wid = _one_off(client, repo, CUSTOM)
    with _orchestrator_db() as c, c:
        c.execute("UPDATE work_items SET one_off_chain = NULL WHERE id = ?", (wid,))
    r = client.post(f"/api/work-items/{wid}/duplicate")
    assert r.status_code == 409 and "record is missing" in r.json()["detail"]
