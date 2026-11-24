"""`POST` and `DELETE /work-items/{wid}/takeover`: a person's terminal takes the
lock on a stopped item's agent session, and every door that would act on the
item meanwhile answers 409."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import psutil
import pytest
from starlette.requests import Request
from support.api import _create_escalation_session, _in_state

from kraft import store
from kraft.api.routes import lifecycle

DOORS = json.loads((Path(__file__).parent / "lifecycle_doors.json").read_text())
#: An item a takeover takes: stopped with its agent task's session, its worktree on disk.
STATE = DOORS["states"]["failed_agent"]
#: A CLI that is gone (pid 1 never has this start time), and one that is running: this test.
GONE = {"pid": 1, "pid_start_time": 0}
RUNNING = {"pid": os.getpid(), "pid_start_time": psutil.Process().create_time()}

#: Doors that stay open under a takeover or have no stopped item to refuse.
EXEMPT = {
    "pause": "stops a running item: a taken-over one is not running",
    "takeover": "the door itself",
    "archive": "an ended item has no session to take",
    "restore": "an ended item has no session to take",
}


def _lock(client, wid):
    with closing(sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")) as c:
        c.row_factory = sqlite3.Row
        return store.takeover_lock(c, wid)


def _take(client, wid, body=RUNNING):
    r = client.post(f"/api/work-items/{wid}/takeover", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_post_then_delete_round_trips_the_lock(client, repo):
    wid = _in_state(client, repo, STATE)

    taken = _take(client, wid)

    assert set(taken) == {"argv", "cwd", "session_id", "resumed_from"}
    assert taken["argv"][-2:] == ["--session-id", taken["session_id"]]
    assert taken["resumed_from"] is None  # the item's last session failed: nothing to fork
    lock = _lock(client, wid)
    assert (lock["takeover_pid"], lock["takeover_session_id"]) == (
        RUNNING["pid"],
        taken["session_id"],
    )
    assert client.get(f"/api/work-items/{wid}").json()["status"] == STATE["status"]

    r = client.delete(f"/api/work-items/{wid}/takeover")

    assert r.status_code == 200, r.text
    assert r.json() == {"status": STATE["status"], "recorded": False, "warnings": []}
    assert _lock(client, wid) is None


def test_delete_with_no_takeover_is_a_no_op(client, repo):
    wid = _in_state(client, repo, STATE)

    r = client.delete(f"/api/work-items/{wid}/takeover")

    assert r.status_code == 200, r.text
    assert r.json()["recorded"] is False


def test_takeover_refuses_a_non_local_peer(client):
    """A loopback-bound server never sees one; a server bound for remote access does."""
    request = Request({"type": "http", "headers": [], "client": ("10.0.0.5", 54321)})

    with pytest.raises(lifecycle.HTTPException) as refused:
        lifecycle._takeover_caller(client.app.state, request, "w1")

    assert refused.value.status_code == 403
    assert "machine Kraft runs on" in refused.value.detail


@pytest.mark.parametrize("verb", ["post", "delete"])
def test_takeover_refuses_a_worker_session(client, repo, verb):
    wid = _in_state(client, repo, STATE)
    _create_escalation_session(wid, "s9", STATE["node"], hook_point="implementation.main.x")

    r = client.request(
        verb,
        f"/api/work-items/{wid}/takeover",
        json=GONE,
        headers={"X-Kraft-Session-Id": f"{wid}/s9"},
    )

    assert r.status_code == 403, r.text
    assert "cannot act on its own work item" in r.text
    assert _lock(client, wid) is None


def test_a_refused_takeover_is_a_409_with_the_reason(client, repo):
    wid = _in_state(client, repo, DOORS["states"]["running"])

    r = client.post(f"/api/work-items/{wid}/takeover", json=GONE)

    assert r.status_code == 409, r.text
    assert "takeover needs paused or needs_human" in r.text


def _doors_refused_while_taken_over():
    return sorted(set(DOORS["doors"]) - set(EXEMPT))


def test_every_door_is_refused_or_exempt():
    """A door added to the table must choose: refuse while a person has the
    session, or say why it need not (`EXEMPT`)."""
    assert set(EXEMPT) <= set(DOORS["doors"])


@pytest.mark.parametrize("door", _doors_refused_while_taken_over())
def test_a_door_refuses_while_a_person_has_the_session(client, repo, door):
    wid = _in_state(client, repo, STATE)
    _take(client, wid)
    spec = DOORS["doors"][door]
    before = client.get(f"/api/work-items/{wid}/events").json()

    r = client.post(f"/api/work-items/{wid}/{spec['path']}", json=spec["body"])

    assert r.status_code == 409, r.text
    assert "a person has this item's session open" in r.text
    assert f"kraft item takeover {wid} --release" in r.text
    # It wrote nothing: the item is as the person left it.
    assert client.get(f"/api/work-items/{wid}/events").json() == before
    assert _lock(client, wid) is not None


@pytest.mark.parametrize(
    "patch",
    [
        pytest.param({"chain_template": "quick-task"}, id="set-chain"),
        pytest.param({"agent_overrides": {"model": "opus"}}, id="set-overrides"),
        pytest.param({"policy": {"budget_usd": 3}}, id="set-policy"),
    ],
)
def test_a_patch_of_what_the_session_runs_under_refuses(client, repo, patch):
    wid = _in_state(client, repo, STATE)
    _take(client, wid)

    r = client.patch(f"/api/work-items/{wid}", json=patch)

    assert r.status_code == 409, r.text
    assert "a person has this item's session open" in r.text


def test_pause_still_answers_as_it_did(client, repo):
    wid = _in_state(client, repo, STATE)
    _take(client, wid)

    r = client.post(f"/api/work-items/{wid}/pause")

    assert r.status_code == 409
    assert "not running" in r.text  # the status's own answer, not the takeover's


def test_a_stale_lock_is_released_by_the_next_door(client, repo):
    """The CLI was killed hard: the pid the lock names is gone, so the next verb
    finds the item free instead of wedged."""
    wid = _in_state(client, repo, STATE)
    _take(client, wid, GONE)

    r = client.post(f"/api/work-items/{wid}/cancel", json={"reason": "r"})

    assert r.status_code == 200, r.text
    assert _lock(client, wid) is None
