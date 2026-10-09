"""The migration that added the `queued` status and its column."""

from support import schema
from test_db_migrations import _build_old_db

from kraft import db


def test_migrate_v52_to_v53_rebuilds_work_items_and_keeps_every_column(tmp_path):
    """Migration 52 rebuilds `work_items` from literal text to widen the status
    CHECK. A column its INSERT forgot would be dropped without an error, so
    every column of an old row is filled and read back."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 52)
    schema.insert_item(conn, status="needs_human")
    filled = {
        r["name"]: (7 if r["type"] in ("INTEGER", "REAL") else f"v-{r['name']}")
        for r in conn.execute("PRAGMA table_info(work_items)")
        if r["name"] not in ("id", "status")
    }
    conn.execute(
        f"UPDATE work_items SET {', '.join(f'{c} = ?' for c in filled)}", tuple(filled.values())
    )
    conn.commit()
    conn.close()

    conn = db._connect(path)
    db.migrate(conn)

    row = dict(conn.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone())
    assert {c: row[c] for c in filled} == filled
    assert (row["status"], row["queued_request"]) == ("needs_human", None)
    conn.execute("UPDATE work_items SET status = 'queued' WHERE id = 'w1'")
