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
#: Doors that answer 200 and change nothing, so `actions` leaves them out:
#: archive on an archived item, and unblock on one that comes after nothing.
NO_OP = {("archived", "archive")} | {
    (state, "unblock") for state, row in DOORS["states"].items() if not row.get("depends_on")
}


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


def _statements(client) -> int:
    """How many SQL statements one `GET /work-items` runs."""
    ran: list[str] = []

    def count(c):
        c.set_trace_callback(ran.append)
        return c.execute("SELECT 1").fetchone()

    # The reader belongs to the server's loop thread.
    client.portal.call(lambda: client.app.state.db.read(count))
    client.get("/api/work-items")
    client.portal.call(lambda: client.app.state.db.read(lambda c: c.set_trace_callback(None)))
    return len(ran)


def test_the_list_reads_no_per_item_facts(client, repo, monkeypatch):
    """The facts a door reads per item come from grouped queries on the
    list: none of the per-item readers is called, and three more items run
    no more statements."""
    from kraft import escalate, executor, store

    for state in ("question", "gate_must_fix", "paused_agent", "failed_after_abandoned"):
        _in_state(client, repo, DOORS["states"][state])
    before = _statements(client)
    for state in ("paused_agent", "gate_must_fix", "blocked"):
        _in_state(client, repo, DOORS["states"][state])
    assert _statements(client) == before

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


def _legacy(client, repo, state: str) -> str:
    """An item in `state`, then stripped to the shape the legacy loader filed."""
    wid = _in_state(client, repo, DOORS["states"][state])
    legacy = {"nodes": [{"id": "spec_approval"}, {"id": "implementation"}]}
    with closing(sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")) as c, c:
        c.execute(
            "UPDATE work_items SET materialized_chain = NULL, run_chain = NULL, "
            "chain_definition = ? WHERE id = ?",
            (json.dumps(legacy), wid),
        )
    return wid


@pytest.mark.parametrize("state", ["paused_agent", "not_started", "question", "failed", "gate"])
def test_a_legacy_row_still_lists(client, repo, state):
    """A row the legacy loader filed has no materialized chain: nothing walks
    it, so no door that walks it (resume, retry, skip, a gate's) is offered,
    nor steer, whose note no walk would read, and the list still answers."""
    wid = _legacy(client, repo, state)

    actions = _listed(client, wid, False)["actions"]

    walking = {"resume", "retry", "skip", "steer", "approve", "reject", "raise_budget"}
    assert not walking & set(actions)
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


@pytest.mark.parametrize("state", ["paused_agent", "not_started", "question", "failed", "gate"])
def test_a_legacy_rows_detail_carries_the_lists_actions(client, repo, state):
    """The detail renders with no restart target, even at a pending gate."""
    wid = _legacy(client, repo, state)

    detail = client.get(f"/api/work-items/{wid}")

    assert detail.status_code == 200, detail.text
    assert bool(detail.json()["pending_gate"]) == (state == "gate")
    assert detail.json()["actions"] == _listed(client, wid, False)["actions"]
    assert (detail.json()["fix_target"], detail.json()["reject_default"]) == (None, None)


@pytest.mark.parametrize(
    ("state", "method", "path", "body"),
    [
        ("gate", "get", "fix-target", None),
        ("gate", "post", "gates/spec_approval/approve", {}),
        ("gate", "post", "gates/spec_approval/reject", {"note": "no"}),
        ("gate", "post", "gates/spec_approval/review", {"outcome": "request_changes"}),
        ("gate", "post", "review", {"outcome": "request_changes"}),
        ("failed", "post", "retry", {}),
        ("gate", "post", "skip", {"path": "spec_approval"}),
        ("paused_agent", "post", "skip", {"path": "implementation"}),
        ("paused_agent", "post", "resume", {}),
        ("not_started", "post", "resume", {}),
        ("question", "post", "resume", {}),
        ("not_started", "post", "steer", {"text": "x"}),
        ("question", "post", "steer", {"text": "x"}),
        ("mr_closed", "post", "reopen-mr", {}),
        ("budget_item_cap", "post", "budget/raise", {"budget_usd": 5}),
    ],
    ids=[
        "fix-target",
        "approve",
        "reject",
        "gate-review",
        "item-review",
        "retry",
        "skip-path-gate",
        "skip-path-paused",
        "resume-paused",
        "resume-not-started",
        "resume-question",
        "steer-not-started",
        "steer-question",
        "reopen-mr",
        "raise-budget",
    ],
)
def test_a_legacy_rows_walking_doors_refuse(client, repo, monkeypatch, state, method, path, body):
    """A legacy row has no chain to find a gate in, restart from, skip along
    or resume: every door that walks it, or leaves a steer for a walk, answers
    409, not a 500 or a walk that stops the item `needs_human`, and changes
    nothing first (a reopened merge request, a raised cap)."""
    from kraft.adapters import forge as forge_mod

    # A forge that would reopen: only the refusal keeps `mr_reopened` off.
    monkeypatch.setattr("kraft.adapters.forge.run.backend_for", lambda *a, **k: "fake")
    monkeypatch.setattr("kraft.adapters.forge.run.resolve", lambda name: forge_mod.FakeForge())
    wid = _legacy(client, repo, state)
    kw = {"json": body} if body is not None else {}
    before = client.get(f"/api/work-items/{wid}/events").json()

    r = getattr(client, method)(f"/api/work-items/{wid}/{path}", **kw)

    assert r.status_code == 409, r.text
    assert client.get(f"/api/work-items/{wid}/events").json() == before
