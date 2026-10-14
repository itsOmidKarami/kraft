"""A session's id is 6 characters, unique only within its work item, and
every lookup by it names the item too (spec "Item files and IDs", part 3)."""

from __future__ import annotations

import pytest
from support.schema import insert_item, insert_session

from kraft import ids, store


async def _two_items_sharing(database, sid="abc123"):
    """Items `w1` and `w2`, each with a running session `sid`."""
    for wid in ("w1", "w2"):
        await database.write(lambda c, w=wid: insert_item(c, wid=w))
        await database.write(lambda c, w=wid: insert_session(c, sid=sid, wid=w, status="running"))


async def test_two_items_may_each_have_a_session_with_the_same_id(database):
    await _two_items_sharing(database)

    rows = database.read(
        lambda c: c.execute(
            "SELECT work_item_id FROM worker_sessions WHERE id = 'abc123' ORDER BY work_item_id"
        ).fetchall()
    )
    assert [r[0] for r in rows] == ["w1", "w2"]


async def test_a_new_session_id_is_six_characters_its_item_has_not_used(database, monkeypatch):
    await database.write(lambda c: insert_item(c, wid="w1"))
    await database.write(lambda c: insert_session(c, sid="aaaaaa", wid="w1"))
    draws = iter("a" * 6 + "b" * 6)
    monkeypatch.setattr(ids.secrets, "choice", lambda _alphabet: next(draws))

    assert database.read(lambda c: store.new_session_id(c, "w1")) == "bbbbbb"


async def test_a_session_id_another_item_has_is_free_for_this_one(database, monkeypatch):
    await database.write(lambda c: insert_item(c, wid="w1"))
    await database.write(lambda c: insert_item(c, wid="w2"))
    await database.write(lambda c: insert_session(c, sid="aaaaaa", wid="w2"))
    monkeypatch.setattr(ids.secrets, "choice", lambda _alphabet: "a")

    assert database.read(lambda c: store.new_session_id(c, "w1")) == "aaaaaa"


_EGRESS = {"phase": "runtime", "allow": [], "deny": []}

#: (a write to `w2`'s session `abc123`, the column it changes, w1's value, w2's value)
_WRITES = [
    (
        lambda c: store.session_exited(c, "abc123", "done", work_item_id="w2"),
        "status",
        "running",
        "done",
    ),
    (
        lambda c: store.session_running(c, "abc123", 42, 1.0, work_item_id="w2"),
        "pid",
        None,
        42,
    ),
    (
        lambda c: store.session_unknown(c, "abc123", work_item_id="w2"),
        "status",
        "running",
        "unknown",
    ),
    (
        lambda c: store.stop_escalation_session(c, "w2", "abc123"),
        "status",
        "running",
        "paused",
    ),
    (
        lambda c: store.pause_work_item(c, "w2", ["abc123"]),
        "status",
        "running",
        "paused",
    ),
    (
        lambda c: store.set_session_egress(c, "abc123", _EGRESS, work_item_id="w2"),
        "egress",
        None,
        '{"phase": "runtime", "allow": [], "deny": []}',
    ),
]


@pytest.mark.parametrize(
    ("write", "column", "w1_value", "w2_value"),
    _WRITES,
    ids=["exited", "running", "unknown", "escalation-stopped", "item-paused", "egress"],
)
async def test_a_lookup_finds_only_its_own_item_s_session(
    database, write, column, w1_value, w2_value
):
    await _two_items_sharing(database)

    await database.write(write)

    values = {
        r["work_item_id"]: r[column]
        for r in database.read(
            lambda c: c.execute(
                f"SELECT work_item_id, {column} FROM worker_sessions WHERE id = 'abc123'"
            ).fetchall()
        )
    }
    assert values == {"w1": w1_value, "w2": w2_value}
    status = {
        w: database.read(lambda c, w=w: store.session_status(c, "abc123", work_item_id=w))
        for w in ("w1", "w2")
    }
    assert status == {
        "w1": "running",
        "w2": w2_value if column == "status" else "running",
    }
