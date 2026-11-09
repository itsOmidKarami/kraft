"""A sliced item on the board: children are not rows, their parent carries a summary."""

from __future__ import annotations

import pytest
from support.api import (
    _create_escalation_session,
    _orchestrator_db,
    _paused,
    _seed_session,
    _statements,
)
from support.frozen import v1_frozen

from kraft import events, item_paths, store


def _family(client, repo, statuses: tuple[str, ...]) -> tuple[str, list[str]]:
    """A parent forked at generation 1, with a child of that generation in each
    of `statuses`, in slice order; the parent and its children's ids."""
    parent = _paused(client, repo, chain_template="default")
    kids = [_paused(client, repo, chain_template="default") for _ in statuses]
    state = store.GroupState(group="slices", phase="forked", generation=1)
    with _orchestrator_db() as conn, conn:
        conn.execute(
            "UPDATE work_items SET group_state = ? WHERE id = ?", (state.to_json(), parent)
        )
        for key, (kid, status) in enumerate(zip(kids, statuses, strict=True), 1):
            conn.execute(
                "UPDATE work_items SET parent_id = ?, slice_generation = 1, slice_key = ?, "
                "status = ? WHERE id = ?",
                (parent, key, status, kid),
            )
    return parent, kids


def test_children_list_under_their_parent_not_as_rows(client, repo):
    loner = _paused(client, repo, chain_template="default")
    parent, kids = _family(client, repo, ("completed", "needs_human", "paused"))
    # An earlier generation's child is no longer the parent's.
    stale = _paused(client, repo, chain_template="default")
    _seed_session(client, kids[0], session_id="s-1", hook_point="implement")
    with _orchestrator_db() as conn, conn:
        conn.execute(
            "UPDATE work_items SET parent_id = ?, slice_generation = 0, slice_key = 1 WHERE id = ?",
            (parent, stale),
        )
        conn.execute("UPDATE worker_sessions SET cost_usd = 0.42 WHERE id = 's-1'")

    rows = {i["id"]: i for i in client.get("/api/work-items").json()["items"]}

    assert parent in rows
    assert not {*kids, stale} & set(rows)
    assert rows[parent]["slices"] == {
        "done": 1, "needs_you": 1, "paused": 1, "running": 0, "queued": 0, "dropped": 0, "total": 3,
    }  # fmt: skip
    detail = client.get(f"/api/work-items/{parent}").json()
    # A slice that needs a person puts its parent under Needs you, on the list
    # and the detail alike: the board merges the detail over the row.
    assert rows[parent]["display_status"] == detail["display_status"] == "needs_you"
    assert rows[parent]["children"] == detail["children"]
    assert [(k["slice_key"], k["status"], k["cost_usd"]) for k in detail["children"]] == [
        (1, "completed", 0.42),
        (2, "needs_human", 0.0),
        (3, "paused", 0.0),
    ]
    assert (rows[loner]["children"], rows[loner]["slices"]) == (None, None)
    assert (rows[parent]["parent_id"], rows[parent]["slice_key"]) == (None, None)
    child = client.get(f"/api/work-items/{kids[1]}").json()
    assert (child["parent_id"], child["slice_key"], child["children"]) == (parent, 2, None)


def test_the_list_reads_children_in_one_pass(client, repo, monkeypatch):
    """Five children cost the list no more statements than two, and no
    per-child usage rollup."""
    _family(client, repo, ("completed", "paused"))
    before = _statements(client)
    _family(client, repo, ("completed", "needs_human", "paused", "queued", "active"))

    assert _statements(client) == before

    def per_child(*_a, **_k):
        raise AssertionError("the list rolled up a child's usage on its own")

    monkeypatch.setattr(store, "usage_rollup", per_child)
    assert client.get("/api/work-items").status_code == 200


@pytest.mark.parametrize(
    ("stop", "shown"),
    [
        ("failed", "failed"),
        ("escalated", "escalated"),
        ("gate", "needs_you"),
        ("question", "needs_you"),
        ("active", "running"),
    ],
    ids=["failure-kind", "live-escalation", "pending-gate", "non-failure-kind", "running"],
)
def test_a_slice_shows_its_own_stop_on_the_list_and_the_detail(client, repo, stop, shown):
    """A slice's badge follows the rule a parent row's does: a failed slice
    reads `failed`, one an escalation turn is working on `escalated`."""
    parent, (kid,) = _family(client, repo, ("paused",))
    with _orchestrator_db() as c, c:
        if stop == "gate":
            store.request_gate(c, kid, "plan_review", "plan")
        elif stop == "active":
            c.execute("UPDATE work_items SET status = 'active' WHERE id = ?", (kid,))
        else:
            kind = "failed" if stop == "escalated" else stop
            store.mark_needs_human(c, kid, "implement", "r", kind=kind)
        if stop == "escalated":
            events.append(c, kid, "escalation_message", {"session_id": "e1"})
    if stop == "escalated":
        # Its own connection: inside the block above it would wait on the lock.
        _create_escalation_session(kid, "e1", "implement")

    rows = {i["id"]: i for i in client.get("/api/work-items").json()["items"]}
    detail = client.get(f"/api/work-items/{parent}").json()
    assert [k["display_status"] for k in rows[parent]["children"]] == [shown]
    assert [k["display_status"] for k in detail["children"]] == [shown]


#: A chain whose implementation and verification revision wrapped in a group.
RUN = [{"id": "run", "kind": "subprocess", "command": "true"}]
GROUPED = [
    {
        "id": "slices",
        "kind": "group",
        "nodes": [
            {"id": n, "kind": "exec", "tasks": RUN} for n in ("implementation", "verification")
        ],
        "unforked": ["implementation"],
    },
    {"id": "after", "kind": "exec", "tasks": RUN},
]
TASKS = "### Task 1: a\n\n### Task 2: b\n\n### Task 3: c\n\n### Task 4: d\n"
SLICES = "## Slices\n\n- **Slice 1: Parser.** Tasks 2-3.\n- **Slice 2: CLI.** Task 4.\n\n"
SPLIT = {
    "groundwork": [1],
    "slices": [
        {"key": 1, "title": "Parser", "tasks": [2, 3]},
        {"key": 2, "title": "CLI", "tasks": [4]},
    ],
}
NO_SPLIT = {"groundwork": [], "slices": []}


@pytest.mark.parametrize(
    ("stored", "plan", "shown"),
    [
        (None, SLICES, {"phase": None, **SPLIT, "joined": 0}),
        ("deciding", SLICES, {"phase": "deciding", **SPLIT, "joined": 2}),
        ("prelude", SLICES, {"phase": "groundwork", **SPLIT, "joined": 2}),
        ("forked", SLICES, {"phase": "slices", **SPLIT, "joined": 2}),
        ("joining", SLICES, {"phase": "joining", **SPLIT, "joined": 2}),
        ("joined", SLICES, {"phase": "joined", **SPLIT, "joined": 2}),
        ("deciding", "", {"phase": "deciding", **NO_SPLIT, "joined": 2}),
    ],
    ids=["unreached", "deciding", "groundwork", "slices", "joining", "joined", "no-section"],
)
def test_the_detail_names_the_groups_phase_and_the_plans_split(client, repo, stored, plan, shown):
    wid = _paused(client, repo, chain_template="default")
    assert client.get(f"/api/work-items/{wid}").json()["group"] is None  # a chain with no group
    docs = item_paths.docs_dir(client.app.state.run_dirs, wid)
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "plan.md").write_text("# Plan\n\n" + plan + TASKS)
    state = store.GroupState(group="slices", phase=stored, joined_through=2) if stored else None
    with _orchestrator_db() as conn, conn:
        conn.execute(
            "UPDATE work_items SET materialized_chain = ?, group_state = ? WHERE id = ?",
            (v1_frozen(GROUPED, repo=str(repo)).to_json(), state and state.to_json(), wid),
        )

    assert client.get(f"/api/work-items/{wid}").json()["group"] == {"id": "slices", **shown}
