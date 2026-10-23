"""The migration that renamed the stored `task_progress` events."""

from test_db_migrations import _build_old_db

from kraft import db


def test_migrate_v33_to_v34_renames_stored_task_progress_events(tmp_path):
    """Kraft-7hy7x: `task_progress` became `plan_progress`, and every reader
    (the Timeline's grouping, the board's "Task N of M") matches the new name
    only. An item filed before the rename keeps its progress because the stored
    rows are renamed once, here, rather than every reader learning both."""
    conn = db._connect(tmp_path / "orchestrator.db")
    _build_old_db(conn, 33)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','default','{}',"
        "'active','now','now')"
    )
    for type_ in ("task_progress", "node_started", "task_progress"):
        conn.execute(
            "INSERT INTO events (work_item_id, type, payload, created_at) "
            "VALUES ('w1', ?, '{\"task\": 1, \"total\": 3}', 'now')",
            (type_,),
        )
    conn.commit()

    db.migrate(conn)

    types = [r[0] for r in conn.execute("SELECT type FROM events ORDER BY seq")]
    assert types == ["plan_progress", "node_started", "plan_progress"]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
