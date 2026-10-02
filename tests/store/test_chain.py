"""The override writes: a node's fields merge, `None` dropping one; the
item-wide `agent_overrides` is replaced whole."""

from __future__ import annotations

import json

import pytest
from support.store_fixtures import CHAIN

from kraft import db, store


@pytest.fixture
def conn(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    fields = {"bead_id": "B-1", "title": "t", "repo": "/r", "chain_template": "quick-task"}
    store.create_work_item(conn, id="w1", chain_definition=CHAIN, **fields)
    yield conn
    conn.close()


def _stored(conn, column: str):
    raw = conn.execute(f"SELECT {column} FROM work_items WHERE id = 'w1'").fetchone()[0]
    return json.loads(raw) if raw else None


def test_merge_fields_sets_keeps_and_drops():
    assert store.merge_fields({"a": 1, "b": 2}, {"b": None, "c": 3}) == {"a": 1, "c": 3}


def test_a_null_node_field_drops_only_that_field_in_the_same_write(conn):
    store.set_node_overrides(
        conn, "w1", {"plan": {"model": "opus", "effort": "high", "extra_prompt": "x"}}
    )
    assert store.set_node_overrides(conn, "w1", {"plan": {"model": None}}) == {
        "plan": {"effort": "high", "extra_prompt": "x"}
    }


def test_two_partial_node_writes_merge(conn):
    store.set_node_overrides(conn, "w1", {"plan": {"model": "opus"}})
    store.set_node_overrides(conn, "w1", {"plan": {"attempts": 3}, "verify": {"effort": "low"}})
    assert _stored(conn, "node_overrides") == {
        "plan": {"model": "opus", "attempts": 3},
        "verify": {"effort": "low"},
    }


def test_a_node_whose_last_field_is_dropped_goes(conn):
    store.set_node_overrides(conn, "w1", {"plan": {"model": "opus"}, "verify": {"effort": "low"}})
    store.set_node_overrides(conn, "w1", {"plan": {"model": None}})
    assert _stored(conn, "node_overrides") == {"verify": {"effort": "low"}}


def test_agent_overrides_are_replaced_whole_and_a_null_field_is_dropped(conn):
    """As in 1.4: a write names the whole override, so a field it leaves out
    is gone. A `None` is dropped rather than stored."""
    store.replace_agent_overrides(conn, "w1", {"model": "opus"})
    assert store.replace_agent_overrides(conn, "w1", {"effort": "high"}) == {"effort": "high"}
    assert _stored(conn, "agent_overrides") == {"effort": "high"}
    assert store.replace_agent_overrides(conn, "w1", {"model": "opus", "effort": None}) == {
        "model": "opus"
    }
    store.replace_agent_overrides(conn, "w1", {"model": None})
    assert _stored(conn, "agent_overrides") is None


def test_an_empty_agent_patch_clears_every_field(conn):
    store.replace_agent_overrides(conn, "w1", {"model": "opus", "effort": "high"})
    assert store.replace_agent_overrides(conn, "w1", {}) == {}
    assert _stored(conn, "agent_overrides") is None
