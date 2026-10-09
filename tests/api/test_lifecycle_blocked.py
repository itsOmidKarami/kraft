"""An item that comes after others: what starting, pausing and deciding a gate
do while one of them is unfinished."""

import dataclasses
import json
from pathlib import Path

import pytest
from support.api import _come_after, _in_state, _paused, _set_status, _status_of

DOORS = json.loads((Path(__file__).parent / "lifecycle_doors.json").read_text())

_STATE = {"resume": "not_started", "retry": "failed"}


@pytest.mark.parametrize("verb", ["resume", "retry"])
def test_a_start_with_an_unfinished_dependency_is_blocked(client, repo, verb):
    """Blocked, not queued, on a full board: dependencies are asked about
    before capacity, so the answer names what the item waits on."""
    client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)
    busy = _in_state(client, repo, DOORS["states"]["running"])
    dep = _paused(client, repo, chain_template="default")
    wid = _in_state(client, repo, DOORS["states"][_STATE[verb]])
    _come_after(wid, [dep])

    r = client.post(f"/api/work-items/{wid}/{verb}", json={})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "blocked"
    assert [(d["id"], d["status"]) for d in r.json()["waiting_on"]] == [(dep, "paused")]
    assert _status_of(client, wid) == "blocked"
    assert _status_of(client, busy) == "active"


def test_a_start_after_an_abandoned_item_is_refused(client, repo):
    dep = _paused(client, repo, chain_template="default")
    wid = _paused(client, repo, chain_template="default")
    _come_after(wid, [dep])
    _set_status(dep, "abandoned")

    r = client.post(f"/api/work-items/{wid}/resume", json={})

    assert r.status_code == 409, r.text
    assert dep in r.json()["detail"] and "kraft item unblock" in r.json()["detail"]
    assert _status_of(client, wid) == "paused"


def test_a_start_whose_dependencies_completed_is_not_held(client, repo, monkeypatch):
    from kraft.api import deps

    monkeypatch.setattr(deps, "spawn", lambda app, w, coro: deps.discard(coro))
    dep = _paused(client, repo, chain_template="default")
    wid = _paused(client, repo, chain_template="default")
    _come_after(wid, [dep])
    _set_status(dep, "completed")

    r = client.post(f"/api/work-items/{wid}/resume", json={})

    # Not held: the door went on to claim and start. (`spawn` is discarded
    # here, so the status afterwards is the bracket's, not a running walk's.)
    assert r.status_code == 200, r.text
    assert r.json().get("status") not in ("blocked", "queued")


def test_pausing_a_blocked_item_puts_it_back_and_keeps_what_it_comes_after(client, repo):
    dep = _paused(client, repo, chain_template="default")
    wid = _paused(client, repo, chain_template="default")
    _come_after(wid, [dep])
    assert client.post(f"/api/work-items/{wid}/resume", json={}).json()["status"] == "blocked"

    r = client.post(f"/api/work-items/{wid}/pause", json={})

    assert r.status_code == 200, r.text
    item = client.get(f"/api/work-items/{wid}").json()
    assert item["status"] == "paused"
    assert [d["id"] for d in item["dependencies"]] == [dep]
    # Started again, it is blocked again.
    assert client.post(f"/api/work-items/{wid}/resume", json={}).json()["status"] == "blocked"
