"""The migration that rebuilt `worker_sessions` for the `merge_rejected` status."""

import sqlite3

import pytest
from support import schema
from test_db_migrations import _build_old_db

from kraft import db


def test_migrate_v55_to_v56_admits_merge_rejected_and_keeps_rowids(tmp_path):
    """v55's worker_sessions CHECK has no 'merge_rejected' (the forge's merge queue
    dropped the request). The table is rebuilt from literal text; every row keeps
    its `rowid`, which readers order by, and the status index comes back."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 55)
    schema.insert_item(conn)
    for rowid, sid in ((7, "s1"), (3, "s2")):
        conn.execute(
            "INSERT INTO worker_sessions (rowid, id, work_item_id, node_id, hook_point, "
            "log_path, result_path, status, created_at) "
            "VALUES (?, ?, 'w1', 'n', 'h', '/l', '/r', 'done', 'now')",
            (rowid, sid),
        )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE worker_sessions SET status = 'merge_rejected' WHERE id = 's1'")
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    conn2.execute("UPDATE worker_sessions SET status = 'merge_rejected' WHERE id = 's1'")
    rows = conn2.execute("SELECT rowid, id, status FROM worker_sessions ORDER BY rowid").fetchall()
    assert [(r["rowid"], r["id"], r["status"]) for r in rows] == [
        (3, "s2", "done"),
        (7, "s1", "merge_rejected"),
    ]
    index_names = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_worker_sessions_status", "idx_worker_sessions_id"} <= index_names
