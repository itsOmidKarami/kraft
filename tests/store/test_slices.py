"""Parents and children of a slice group: the rows `file_children` writes, the
state a parent keeps, and a parent waiting for its children."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from types import SimpleNamespace

import pytest
from support.harness import v1_chain

from kraft import store, waits
from kraft.store.slices import ChildRow, GroupState
from kraft.vocab import WorkItemStatus

_NODES = [
    {
        "id": "implementation",
        "kind": "exec",
        "tasks": [{"id": "run", "kind": "subprocess", "command": "true"}],
    }
]


def _parent(conn: sqlite3.Connection, chain: str, wid: str = "p1") -> None:
    store.create_work_item(
        conn,
        id=wid,
        bead_id=None,
        title="the parent",
        repo="/r",
        chain_template="default",
        chain_definition="{}",
        materialized_chain=chain,
        status=WorkItemStatus.ACTIVE,
    )


def _child(chain: str, key: int, wid: str | None = None) -> ChildRow:
    return ChildRow(
        id=wid or f"c{key}",
        title=f"the parent · slice {key}: S",
        slice_key=key,
        materialized_chain=chain,
        base_ref="abc123",
        attachments=[{"kind": "plan", "path": f".kraft/slice-{key}/plan.md"}],
        repo="/r",
    )


@pytest.fixture
def chain(tmp_path) -> str:
    return v1_chain(_NODES, repo=str(tmp_path)).to_json()


def test_group_state_round_trips():
    state = GroupState(
        group="slices",
        phase="joining",
        generation=2,
        fork_commit="f00",
        children=["c1", "c2"],
        joined_through=1,
        at_commit="x",
        slice_phase="picking",
        recorded={1: ["a", "b"], 2: ["c"]},
        skipped={2: ["verification"]},
    )

    assert GroupState.from_json(state.to_json()) == state


async def test_children_are_filed_together_or_not_at_all(database, chain):
    await database.write(lambda c: _parent(c, chain))
    before = GroupState(group="slices", generation=1)
    await database.write(lambda c: store.set_group_state(c, "p1", before))

    def fork(c):
        store.file_children(
            c,
            "p1",
            GroupState(group="slices", generation=1),
            [_child(chain, 1), _child(chain, 1, "c2")],
        )

    with pytest.raises(sqlite3.IntegrityError):
        await database.write(fork)

    assert database.read(lambda c: store.children_of(c, "p1")) == []
    assert database.read(lambda c: store.group_state(c, "p1")) == before

    def fork_both(c):
        state = GroupState(group="slices", generation=1)
        inherited = dataclasses.replace(
            _child(chain, 2),
            agent_overrides='{"implementation": {"model": "opus"}}',
            submodules=["libs/x"],
            root_merge_policy="together",
        )
        store.file_children(c, "p1", state, [_child(chain, 1), inherited])

    await database.write(fork_both)

    children = database.read(lambda c: store.children_of(c, "p1"))
    assert [(r["id"], r["status"], r["slice_key"], r["base_ref"]) for r in children] == [
        ("c1", "paused", 1, "abc123"),
        ("c2", "paused", 2, "abc123"),
    ]
    state = database.read(lambda c: store.group_state(c, "p1"))
    assert (state.phase, state.children) == ("forked", ["c1", "c2"])
    assert json.loads(children[0]["attachments"])[0]["kind"] == "plan"
    # The parent's harness overrides and cross-repo paths come with the slice, frozen.
    assert (children[1]["agent_overrides"], children[1]["root_merge_policy"]) == (
        '{"implementation": {"model": "opus"}}',
        "together",
    )
    assert json.loads(children[1]["submodules"]) == ["libs/x"]


async def test_a_parent_waiting_on_slices_is_never_due(database, chain):
    await database.write(lambda c: _parent(c, chain))

    await database.write(lambda c: store.mark_waiting_on_slices(c, "p1", "slices"))

    row = database.read(
        lambda c: c.execute("SELECT status, stop_kind, retry_at FROM work_items").fetchone()
    )
    assert (row["status"], row["stop_kind"], row["retry_at"]) == ("waiting", "slices", None)
    app = SimpleNamespace(state=SimpleNamespace(db=database))
    assert await waits.tick(app, now="9999-01-01T00:00:00+00:00") == []


async def test_a_familys_ids_are_the_parent_and_its_children(database, chain):
    await database.write(lambda c: _parent(c, chain))
    await database.write(
        lambda c: store.file_children(
            c, "p1", GroupState(group="slices", generation=1), [_child(chain, 1), _child(chain, 2)]
        )
    )

    assert database.read(lambda c: store.family_ids(c, "c2")) == ["p1", "c1", "c2"]
    assert database.read(lambda c: store.family_ids(c, "p1")) == ["p1", "c1", "c2"]
