"""`POST /work-items/{id}/retry` addresses its target by canonical path. The
route validates and hands the target to `executor.retry`; what each scope
reruns is tests/executor/test_retry_scopes.py."""

from __future__ import annotations

import pytest
from support.api import _force_node, _poll_events, _post_default


@pytest.fixture
def retried(client, monkeypatch):
    """What the route handed `executor.retry`, instead of walking it."""
    from kraft import executor

    seen = {}

    async def fake(*args, **kwargs):
        seen.update(kwargs)
        return "completed"

    monkeypatch.setattr(executor, "retry", fake)
    return seen


def _stopped_at(client, repo, node_id):
    wid = _post_default(client, repo)
    _poll_events(client, wid, "gate_requested")
    _force_node(wid, node_id, "needs_human")
    return wid


@pytest.mark.parametrize(
    ("body", "path", "scope"),
    [
        ({}, "verification", "node"),
        ({"path": "verification.review"}, "verification.review", "step"),
        ({"path": "verification.review.code_review"}, "verification.review.code_review", "task"),
        ({"path": "spec"}, "spec", "node"),
        ({"restart": True}, None, None),
    ],
    ids=["stopped-node", "step", "task", "completed-node", "restart"],
)
def test_a_retry_hands_its_target_to_the_fork(client, repo, retried, body, path, scope):
    wid = _stopped_at(client, repo, "verification")

    r = client.post(f"/api/work-items/{wid}/retry", json=body)

    assert r.status_code == 200, r.text
    assert r.json()["path"] == path
    _wait_for(lambda: "target" in retried)
    target = retried["target"]
    assert (target and target.path, target and target.scope.value) == (path, scope)


@pytest.mark.parametrize(
    ("body", "status", "says"),
    [
        ({"path": "verification.nope"}, 422, "path: 'verification.nope': node"),
        ({"path": "merge"}, 409, "path: 'merge' is after the node the item stands on"),
        ({"path": "spec", "restart": True}, 422, "path: a restart reruns the whole chain"),
        (
            {"path": "verification.review.code_review", "override": {"task": {"model": "x"}}},
            422,
            "task: retry overrides are refused",
        ),
        ({"restart": True, "override": {"task": {"model": "x"}}}, 422, "override: a work-item"),
    ],
    ids=["unknown-path", "forward", "path-and-restart", "override-refused", "override-on-restart"],
)
def test_a_retry_the_route_cannot_honour_is_refused_naming_the_field(
    client, repo, retried, body, status, says
):
    """The item is left exactly as it was: still stopped, nothing forked."""
    wid = _stopped_at(client, repo, "verification")

    r = client.post(f"/api/work-items/{wid}/retry", json=body)

    assert r.status_code == status, r.text
    assert says in r.json()["detail"]
    assert client.get(f"/api/work-items/{wid}").json()["status"] == "needs_human"
    assert retried == {}


def test_an_empty_override_is_no_override(client, repo, retried):
    wid = _stopped_at(client, repo, "verification")

    r = client.post(
        f"/api/work-items/{wid}/retry",
        json={"path": "verification.review.code_review", "override": {}},
    )

    assert r.status_code == 200, r.text
    _wait_for(lambda: "target" in retried)
    assert retried["override"] is None


def _wait_for(predicate, timeout=10.0):
    import time

    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "the retry never reached executor.retry"
        time.sleep(0.02)
