"""Migrations 44 and 45, from UX V2 W4 (`work_items.stop_kind` clearing on a
move out of the stop set, `events.node_id` backfill), 46 from W7
(`review_viewed`), and the `review_threads` ones: 41 (gate rows kept) and 50
(`start_side` and `quote`). A sibling of test_db_migrations.py."""

import sqlite3

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


def test_migrate_v41_to_v42_keeps_review_gate_rows(tmp_path):
    """v41 -> v42: `reviews.gate` and `review_threads.gate` were NOT NULL; the
    rebuild drops that constraint but a pre-existing gated row keeps its value."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        41,
        replace=(("gate         TEXT,", "gate         TEXT NOT NULL,"),),
    )
    schema.insert_item(conn)
    schema.insert_session(conn)
    conn.execute(
        "INSERT INTO review_threads (id, work_item_id, gate, anchor_sha, created_at) "
        "VALUES ('t1', 'w1', 'g', 'abc', 'now')"
    )
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    row = conn2.execute("SELECT gate FROM review_threads WHERE id='t1'").fetchone()
    assert row["gate"] == "g"


def test_migrate_v50_to_v51_adds_range_sides_and_quotes_keeping_threads(tmp_path):
    """Migration 50: `review_threads` gains `start_side` and `quote`, both NULL
    on a thread written before them, which reads as a range on one side with
    nothing quoted; the thread itself is untouched."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 50)
    schema.insert_item(conn)
    conn.execute(
        "INSERT INTO review_threads (id, work_item_id, gate, file_path, side, start_line, "
        "end_line, anchor_sha, created_at) VALUES ('t1', 'w1', 'spec', 'a.py', 'old', 2, 4, "
        "'sha', 'now')"
    )
    conn.commit()
    assert {"start_side", "quote"}.isdisjoint(schema.columns(conn, "review_threads"))
    conn.close()

    conn = db._connect(path)
    db.migrate(conn)
    assert schema.version(conn) == db.SCHEMA_VERSION
    row = conn.execute("SELECT * FROM review_threads WHERE id = 't1'").fetchone()
    assert (row["side"], row["start_line"], row["end_line"], row["anchor_sha"]) == (
        "old",
        2,
        4,
        "sha",
    )
    assert (row["start_side"], row["quote"]) == (None, None)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE review_threads SET start_side = 'both' WHERE id = 't1'")
