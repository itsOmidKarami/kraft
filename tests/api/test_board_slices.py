"""A sliced item on the board: children are not rows, their parent carries a summary."""

from __future__ import annotations

from support.api import _orchestrator_db, _paused, _seed_session, _statements

from kraft import store


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
