"""Replacing, switching away from and duplicating a one-off chain, and when
no chain switch may take."""

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
    echo = r.json()
    assert "one_off_chain" not in echo and echo["chain_template"] == "@one-off"
    expected = {"extends": "default", "custom": 1, "needs_review": True, "approval": None}
    assert echo["one_off"] == expected
    assert client.get(f"/api/work-items/{wid}").json()["one_off"] == expected
    assert [i["one_off"] for i in client.get("/api/work-items").json()["items"]] == [expected]
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


@pytest.mark.parametrize(
    "body", [{"chain": "default"}, {"one_off_chain": OTHER}], ids=["library", "one-off"]
)
@pytest.mark.parametrize(
    ("node", "status", "words"),
    [
        pytest.param("spec", "paused", "already started", id="started"),
        pytest.param(None, "active", "is running", id="active-not-started"),
        pytest.param(None, "abandoned", "is abandoned", id="ended-never-started"),
    ],
)
def test_patch_refuses_a_chain_switch_once_fixed(client, repo, body, node, status, words):
    """`store._CHAIN_SETTABLE`, worded for the item's case."""
    wid = _paused(client, repo, chain_template="quick-task")
    with _orchestrator_db() as c, c:
        c.execute(
            "UPDATE work_items SET current_node_id = ?, status = ? WHERE id = ?",
            (node, status, wid),
        )
    r = client.patch(f"/api/work-items/{wid}", json=body)
    assert r.status_code == 409, r.text
    assert words in r.json()["detail"]
    assert _item_row(wid)["chain_template"] == "quick-task"


def _node_ids(wid) -> set[str]:
    return {n["id"] for n in json.loads(_item_row(wid)["materialized_chain"])["chain"]["nodes"]}


@pytest.mark.parametrize(
    ("filed", "skip", "body", "base_gone"),
    [
        pytest.param(CUSTOM, "local_review", {"chain": "default"}, False, id="to-library"),
        pytest.param(CUSTOM, "local_review", {"one_off_chain": OTHER}, False, id="to-one-off"),
        pytest.param(EXTENDS_QUICK, "implementation", {"chain": "default"}, True, id="base-gone"),
    ],
)
def test_a_switch_from_a_one_off_keeps_its_recorded_skips(
    client, repo, templates_dir, filed, skip, body, base_gone
):
    """The one-off's own `skips` record, never its base re-resolved: still kept
    once the base is gone from the library."""
    wid = _one_off(client, repo, filed, skip_nodes=[skip])
    assert skip not in _node_ids(wid)
    if base_gone:
        _drop_quick_task(templates_dir, client)
    r = client.patch(f"/api/work-items/{wid}", json=body)
    assert r.status_code == 200, r.text
    assert skip not in _node_ids(wid)
