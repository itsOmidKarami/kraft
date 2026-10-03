"""`PATCH /work-items/{id}` `node_overrides` on a started node: locked, except
for the repair a stored-model stop names. A sibling of test_work_items.py."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest
from support.harness import v1_chain

from kraft import overrides, store

WID = "w1"
AGENT = {"id": "implement", "kind": "agent", "harness": "fake", "prompt": "Do it."}


def _run(fn) -> None:
    conn = sqlite3.connect(Path(os.environ["KRAFT_RUN_DIR"]) / "orchestrator.db")
    conn.row_factory = sqlite3.Row
    try:
        fn(conn)
        conn.commit()
    finally:
        conn.close()


def _stopped_on_a_stored_model(repo, node_overrides: dict, *, status="needs_human") -> None:
    """An item carried over from 1.4 with `node_overrides` as 1.4 stored them
    (any text as a model), stopped where the launch refused it: the node has
    started, and the stop names `set-node-override --clear`."""
    chain = v1_chain(
        [{"id": n, "kind": "exec", "tasks": [AGENT]} for n in ("implementation", "review")],
        repo=repo,
    )
    _run(
        lambda c: store.create_work_item(
            c,
            id=WID,
            bead_id=None,
            title="t",
            repo=str(repo),
            chain_template="t",
            chain_definition="{}",
            status="paused",
            materialized_chain=chain.to_json(),
        )
    )
    _run(
        lambda c: c.execute(
            "UPDATE work_items SET node_overrides = ? WHERE id = ?",
            (json.dumps(node_overrides), WID),
        )
    )
    _run(lambda c: store.enter_node(c, WID, "implementation"))
    reason = (
        overrides.stored_model_refusal(WID, {}, node_overrides["implementation"], "implementation")
        or "task failed in node implementation"
    )
    _run(lambda c: store.mark_needs_human(c, WID, "implementation", reason, kind="failed"))
    if status != "needs_human":
        _run(lambda c: c.execute("UPDATE work_items SET status = ? WHERE id = ?", (status, WID)))


def _patch(client, fields: dict):
    return client.patch(
        f"/api/work-items/{WID}", json={"node_overrides": {"implementation": fields}}
    )


@pytest.mark.parametrize(
    ("fields", "left"),
    [({}, {}), ({"model": "valid-model-1"}, {"model": "valid-model-1", "effort": "low"})],
    ids=["clear", "a-valid-model"],
)
def test_the_command_a_stored_model_stop_names_works(client, repo, fields, left):
    """The node started before its launch was refused, so its lock answered
    409 to the `--clear` the stop names, and a retry stopped again: the only
    ways on were skipping the node or editing the database."""
    _stopped_on_a_stored_model(repo, {"implementation": {"model": "bad model", "effort": "low"}})
    stop = client.get(f"/api/work-items/{WID}").json()["stop"]["reason"]
    assert f"kraft item set-node-override {WID} --node implementation --clear" in stop

    r = _patch(client, fields)

    assert r.status_code == 200, r.text
    assert (client.get(f"/api/work-items/{WID}").json()["node_overrides"] or {}).get(
        "implementation", {}
    ) == left


@pytest.mark.parametrize(
    ("stored", "fields", "status"),
    [
        ({"model": "bad model"}, {"auto_escalate": False}, "needs_human"),
        ({"model": "valid-model-1"}, {}, "needs_human"),
        ({"model": "bad model"}, {}, "paused"),
    ],
    ids=["another-field", "a-valid-stored-model", "not-stopped"],
)
def test_a_started_node_stays_locked_for_anything_else(client, repo, stored, fields, status):
    _stopped_on_a_stored_model(repo, {"implementation": stored}, status=status)

    r = _patch(client, fields)

    assert r.status_code == 409
    assert "has started; its config is locked" in r.json()["detail"]
