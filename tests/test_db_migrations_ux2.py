"""Migrations 44 and 45, from UX V2 W4 (`work_items.stop_kind` clearing on a
move out of the stop set, `events.node_id` backfill), and 46 from W7
(`review_viewed`). A sibling of test_db_migrations.py."""

import pytest
from support import schema
from test_db_migrations import _build_old_db

from kraft import db


@pytest.mark.parametrize("new_status", ["active", "paused", "completed", "abandoned"])
def test_stop_kind_clears_on_a_move_out_of_the_stop_set(tmp_path, new_status):
    conn = schema.fresh(tmp_path)
    schema.insert_item(conn, status="needs_human")
    conn.execute("UPDATE work_items SET stop_kind = 'failed' WHERE id = 'w1'")
    conn.execute("UPDATE work_items SET status = ? WHERE id = 'w1'", (new_status,))
    row = conn.execute("SELECT stop_kind FROM work_items WHERE id = 'w1'").fetchone()
    assert row["stop_kind"] is None


def test_stop_kind_survives_a_move_between_stop_statuses(tmp_path):
    conn = schema.fresh(tmp_path)
    schema.insert_item(conn, status="needs_human")
    conn.execute("UPDATE work_items SET stop_kind = 'failed' WHERE id = 'w1'")
    conn.execute("UPDATE work_items SET status = 'needs_human' WHERE id = 'w1'")
    row = conn.execute("SELECT stop_kind FROM work_items WHERE id = 'w1'").fetchone()
    assert row["stop_kind"] == "failed"


def test_events_node_id_backfill(tmp_path):
    """Migration 45 (B13): `events.node_id` is backfilled from whichever of
    `payload.node_id`/`payload.node` is a string; a row with neither, or a
    `node` key that holds something other than a string, keeps NULL."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 45)
    schema.insert_item(conn)
    conn.execute(
        "INSERT INTO events (work_item_id, type, payload, created_at) VALUES "
        "('w1', 'a', '{\"node_id\": \"spec\"}', 'now'), "
        "('w1', 'b', '{\"node\": \"implementation\"}', 'now'), "
        "('w1', 'c', '{\"reason\": \"no node here\"}', 'now'), "
        "('w1', 'd', '{\"node\": {\"not\": \"a string\"}}', 'now')"
    )
    conn.commit()
    conn.close()

    conn = db._connect(path)
    db.migrate(conn)
    rows = {
        r["type"]: r["node_id"]
        for r in conn.execute("SELECT type, node_id FROM events ORDER BY seq").fetchall()
    }
    assert rows == {"a": "spec", "b": "implementation", "c": None, "d": None}


def test_migrate_v46_to_v47_adds_review_viewed(tmp_path):
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 46)
    schema.insert_item(conn)
    conn.commit()
    assert "review_viewed" not in schema.tables(conn)
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert "review_viewed" in schema.tables(conn2)
    assert conn2.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1
