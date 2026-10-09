"""The migrations that rebuilt `work_items` for the `queued` and `blocked` statuses."""

from support import schema
from test_db_migrations import _build_old_db

from kraft import db


def _migrated_with_every_column_filled(tmp_path, version):
    """A `version` database holding one row with every column set, migrated to
    today's schema. Returns (what was written, the row as it is now, conn).

    Both migrations rebuild the table from literal text. A column their INSERT
    forgot would be dropped without an error, so every column is filled."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, version)
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
    return filled, dict(conn.execute("SELECT * FROM work_items WHERE id = 'w1'").fetchone()), conn


def test_migrate_v52_to_v53_rebuilds_work_items_and_keeps_every_column(tmp_path):
    filled, row, conn = _migrated_with_every_column_filled(tmp_path, 52)
    assert {c: row[c] for c in filled} == filled
    assert (row["status"], row["queued_request"]) == ("needs_human", None)
    conn.execute("UPDATE work_items SET status = 'queued' WHERE id = 'w1'")


def test_migrate_v53_to_v54_rebuilds_work_items_and_keeps_every_column(tmp_path):
    """Version 53 already has `queued_request`: a queued item's saved request
    must come through the rebuild."""
    filled, row, conn = _migrated_with_every_column_filled(tmp_path, 53)
    assert "queued_request" in filled
    assert {c: row[c] for c in filled} == filled
    assert (row["status"], row["depends_on"]) == ("needs_human", None)
    conn.execute("UPDATE work_items SET status = 'blocked' WHERE id = 'w1'")
