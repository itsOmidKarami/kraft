"""POST /work-items/{wid}/ci-rerun -- a fix-loop repair re-runs the CI jobs that
failed outside the code (Kraft-zje6v)."""

from __future__ import annotations

import pytest
from support.api import _completed_item, _fake_forge, _force_node

from kraft import events
from kraft.adapters import forge as forge_mod
from kraft.vocab import ChainEvent

NODE = "merge_request_feedback"
_RED = {
    "ci_states": ["failed"],
    "ci_failed_jobs": [(forge_mod.FailedJob("test", "failed", "script_failure"),)],
}


def _append(client, wid, type, payload):
    db = client.app.state.db
    client.portal.call(db.write, lambda c: events.append(c, wid, type, payload))


def _item_in_fix_loop(client, repo, monkeypatch, *, mr_ci=True, open_cycle=True, **forge_fields):
    """A started item on `merge_request_feedback`, with a fix attempt open. The
    quick-task chain has no such node, so `waits_on_mr_ci` is the seam."""
    wid = _completed_item(client, repo)
    _force_node(wid, NODE, "active")
    monkeypatch.setattr("kraft.api.routes.lifecycle.ci_reruns.waits_on_mr_ci", lambda node: mr_ci)
    fake = _fake_forge(monkeypatch, **{**_RED, **forge_fields})
    if open_cycle:
        _append(client, wid, ChainEvent.FIX_CYCLE_STARTED, {"node_id": NODE, "cycle": 1})
    return wid, fake


def _rerun(client, wid, reason="bd download 504"):
    return client.post(f"/api/work-items/{wid}/ci-rerun", json={"reason": reason})


def _recorded(client, wid):
    events_ = client.get(f"/api/work-items/{wid}/events").json()
    return [e for e in events_ if e["type"] == "ci_rerun_requested"]


def test_a_rerun_re_runs_the_failed_jobs_and_records_it(client, repo, monkeypatch):
    wid, fake = _item_in_fix_loop(client, repo, monkeypatch)

    r = _rerun(client, wid)

    assert r.status_code == 200, r.text
    assert r.json() == {"work_item_id": wid, "reran": ["/pipelines"], "jobs": ["test"]}
    assert fake.retried == ["/pipelines"]
    (event,) = _recorded(client, wid)
    assert event["payload"] == {
        "node_id": NODE,
        "cycle": 1,
        "reason": "bd download 504",
        "jobs": ["test"],
        "reran": ["/pipelines"],
    }


@pytest.mark.parametrize(
    ("status", "fields"),
    [
        pytest.param(422, {"reason": "  "}, id="blank-reason"),
        pytest.param(409, {"mr_ci": False}, id="node-without-mr-ci"),
        pytest.param(409, {"open_cycle": False}, id="no-open-cycle"),
        pytest.param(409, {"already": True}, id="already-this-attempt"),
        pytest.param(409, {"ci_states": ["success"]}, id="ci-green"),
        pytest.param(409, {"ci_states": ["pending"]}, id="ci-pending"),
        pytest.param(409, {"ci_shas": ["deadbeef"]}, id="stale-head"),
        pytest.param(409, {"rerun_nothing": True}, id="nothing-re-runnable"),
    ],
)
def test_a_rerun_is_refused(client, repo, monkeypatch, status, fields):
    fields = dict(fields)
    reason = fields.pop("reason", "bd download 504")
    already = fields.pop("already", False)
    wid, fake = _item_in_fix_loop(client, repo, monkeypatch, **fields)
    if already:
        assert _rerun(client, wid).status_code == 200
        fake.retried.clear()
        # the first re-run is on record: count it away from the assertions below
        before = _recorded(client, wid)
    else:
        before = []

    r = _rerun(client, wid, reason)

    assert r.status_code == status, r.text
    assert _recorded(client, wid) == before
    if "rerun_nothing" not in fields:
        assert fake.retried == []


def test_a_rerun_in_the_next_attempt_is_allowed(client, repo, monkeypatch):
    wid, _ = _item_in_fix_loop(client, repo, monkeypatch)
    assert _rerun(client, wid).status_code == 200

    _append(client, wid, ChainEvent.FIX_CYCLE_FINISHED, {"node_id": NODE, "cycle": 1})
    _append(client, wid, ChainEvent.FIX_CYCLE_STARTED, {"node_id": NODE, "cycle": 2})

    assert _rerun(client, wid).status_code == 200
    assert [e["payload"]["cycle"] for e in _recorded(client, wid)] == [1, 2]
