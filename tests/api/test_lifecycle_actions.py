"""`actions` on the list and the detail: the doors `lifecycle_doors.json`
says take the item's state, which `test_a_door_takes_only_the_states_this_table_lists`
holds to the routes."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from support.api import _cancel_task, _in_state, _spawn_never_returning

from kraft.api.routes import board
from kraft.vocab import ItemAction

DOORS = json.loads((Path(__file__).parent / "lifecycle_doors.json").read_text())
#: The table names the raise door by its path.
ACTION = {"budget_raise": "raise_budget"}
#: Archive answers an archived item 200 and changes nothing: not offered.
NO_OP = {("archived", "archive")}


def _listed(client, wid: str, archived: bool) -> dict:
    items = client.get(
        f"/api/work-items?include_abandoned=true&archived={str(archived).lower()}"
    ).json()["items"]
    return next(i for i in items if i["id"] == wid)


@pytest.mark.parametrize("state", list(DOORS["states"]))
def test_actions_are_the_doors_this_table_lists(client, repo, state):
    row = DOORS["states"][state]
    wid = _in_state(client, repo, row)
    if row.get("walk"):
        client.portal.call(lambda: _spawn_never_returning(client.app, wid))
    want = [
        str(a)
        for a in ItemAction
        if any(
            ACTION.get(door, door) == a and state in spec["takes"] and (state, door) not in NO_OP
            for door, spec in DOORS["doors"].items()
        )
    ]

    detail = client.get(f"/api/work-items/{wid}").json()
    listed = _listed(client, wid, bool(row.get("archived")))

    assert detail["actions"] == want
    assert listed["actions"] == want
    if row.get("walk"):
        client.portal.call(lambda: _cancel_task(client.app, wid))


def test_the_list_reads_no_per_item_facts(client, repo, monkeypatch):
    """The facts a door reads per item come from grouped queries on the
    list: none of the per-item readers is called."""
    from kraft import escalate, executor, store

    for state in ("question", "gate_must_fix", "paused_agent", "failed_after_abandoned"):
        _in_state(client, repo, DOORS["states"][state])

    def per_item(*_a, **_k):
        raise AssertionError("the list read a fact per item")

    monkeypatch.setattr(escalate, "escalation_running", per_item)
    monkeypatch.setattr(executor, "resume_steer", per_item)
    monkeypatch.setattr(store, "unmet_dependencies", per_item)
    monkeypatch.setattr(store, "open_must_fix", per_item)
    monkeypatch.setattr(board, "_mr_ref", per_item)
    monkeypatch.setattr(board, "_stop_episode", per_item)

    r = client.get("/api/work-items")

    assert r.status_code == 200, r.text
    assert all("actions" in i for i in r.json()["items"])


@pytest.mark.parametrize("state", ["paused_agent", "failed", "gate"])
def test_a_legacy_row_still_lists(client, repo, state):
    """A row the legacy loader filed has no materialized chain: nothing walks
    it, so nothing is skippable and no agent task is paused, and the list
    still answers."""
    wid = _in_state(client, repo, DOORS["states"][state])
    legacy = {"nodes": [{"id": "spec_approval"}, {"id": "implementation"}]}
    with closing(sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")) as c, c:
        c.execute(
            "UPDATE work_items SET materialized_chain = NULL, run_chain = NULL, "
            "chain_definition = ? WHERE id = ?",
            (json.dumps(legacy), wid),
        )

    actions = _listed(client, wid, False)["actions"]

    assert "skip" not in actions
    assert "steer" not in actions
    assert "cancel" in actions


@pytest.mark.parametrize(
    ("budget", "want"),
    [
        ({"scope": "work_item"}, True),
        ({"scope": "usd", "path": ""}, True),
        ({"scope": "usd", "path": "implementation"}, False),
        ({"scope": "daily"}, False),
        (None, False),
    ],
    ids=["item-cap", "policy-item-wide", "node-cap", "daily-cap", "no-budget"],
)
def test_raisable_is_the_caps_budget_raise_takes(budget, want):
    assert board.raisable({"reason": "r", "budget": budget} if budget else None) is want
