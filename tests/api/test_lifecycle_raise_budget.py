"""`POST /work-items/{id}/budget/raise` on a stop the item-wide `budget_usd` of
the item's own policy made, and a raise whose retry cannot start: a split-off
sibling of `tests/api/test_lifecycle.py`, kept under the repo's line budget.
The item's own cap, and the stops the route refuses, are in
`tests/test_item_overrides.py`."""

from __future__ import annotations

import dataclasses

import pytest
from fastapi import HTTPException
from support.api import _budget_stopped_item, _set_status

from kraft.api.routes import lifecycle

ITEM_WIDE = {"scope": "usd", "path": "", "spent_usd": 0.07, "cap_usd": 0.05, "unknown_launches": 0}
UNKNOWN = {**ITEM_WIDE, "spent_usd": 0.0, "unknown_launches": 2}


def _raised(client, wid):
    return [
        e["payload"]
        for e in client.get(f"/api/work-items/{wid}/events").json()
        if e["type"] == "budget_raised"
    ]


def test_raise_budget_merges_the_policy_budget_and_keeps_every_other_field(
    monkeypatch, client, repo
):
    """`set-policy` replaces the whole override, so following its hint to
    raise `budget_usd` dropped the item's other fields. The route merges
    instead, the way the interface's Raise cap does, and retries."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    wid = _budget_stopped_item(
        client, repo, ITEM_WIDE, policy={"budget_usd": 0.05, "total_time_cap_minutes": 90}
    )

    r = client.post(f"/api/work-items/{wid}/budget/raise", json={"budget_usd": 1.0})

    assert r.status_code == 200, r.text
    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["policy_override"] == {"budget_usd": 1.0, "total_time_cap_minutes": 90}
    assert detail["budget_cap"]["key"] == "policy.budget_usd"
    assert detail["budget_cap"]["cap_usd"] == 1.0
    assert _raised(client, wid) == [{"budget_usd": 1.0, "key": "policy.budget_usd"}]


@pytest.mark.parametrize(
    ("budget_usd", "status", "stored"),
    [(None, 200, "none"), (5.0, 409, 0.05)],
    ids=["no-cap", "a-higher-cap"],
)
def test_raise_budget_on_unknown_spend_takes_only_no_cap(
    monkeypatch, client, repo, budget_usd, status, stored
):
    """No higher cap passes spend a harness never reported, so only `null`
    is written; a number is refused and pointed at `--usd none`."""
    monkeypatch.setenv("KRAFT_FAKE_AGENT", "fix")
    wid = _budget_stopped_item(client, repo, UNKNOWN, policy={"budget_usd": 0.05})

    r = client.post(f"/api/work-items/{wid}/budget/raise", json={"budget_usd": budget_usd})

    assert r.status_code == status, r.text
    if status == 409:
        assert "raise-budget ID --usd none" in r.json()["detail"]
    detail = client.get(f"/api/work-items/{wid}").json()
    assert detail["policy_override"]["budget_usd"] == stored


@pytest.mark.parametrize(
    "breach",
    [ITEM_WIDE, {"scope": "work_item", "spent_usd": 5.0, "cap_usd": 5.0}],
    ids=["policy", "own"],
)
def test_raise_budget_with_every_slot_busy_changes_nothing(client, repo, breach):
    """The retry would 409 on a full board, and it used to after the cap was
    already written: raised, and the item still stopped. Now the route asks
    first, and a refusal leaves the cap where it was."""
    client.app.state.policy = dataclasses.replace(client.app.state.policy, max_concurrent=1)
    wid = _budget_stopped_item(client, repo, breach, policy={"budget_usd": 0.05})
    busy = _budget_stopped_item(client, repo, breach)
    _set_status(busy, "active")
    before = client.get(f"/api/work-items/{wid}").json()

    r = client.post(f"/api/work-items/{wid}/budget/raise", json={"budget_usd": 2.0})

    assert r.status_code == 409, r.text
    assert "slots are busy" in r.json()["detail"]
    assert "cap was not changed" in r.json()["detail"]
    after = client.get(f"/api/work-items/{wid}").json()
    assert after["budget_cap"] == before["budget_cap"]
    assert after["policy_override"] == before["policy_override"]
    assert after["status"] == "needs_human"
    assert _raised(client, wid) == []


def test_a_retry_refused_after_the_raise_says_the_cap_was_raised(monkeypatch, client, repo):
    """The retry's own claim still decides. When it refuses after the write,
    the answer says what happened: raised, and not retried."""

    async def refused(*args, **kwargs):
        raise HTTPException(409, "a walk is already running for this work item")

    monkeypatch.setattr(lifecycle, "retry_work_item", refused)
    wid = _budget_stopped_item(client, repo, ITEM_WIDE, policy={"budget_usd": 0.05})

    r = client.post(f"/api/work-items/{wid}/budget/raise", json={"budget_usd": 2.0})

    assert r.status_code == 409
    assert r.json()["detail"].startswith(
        "the cap was raised to $2, but the retry was refused: a walk is already running"
    )
    assert "kraft item retry" in r.json()["detail"]
