"""`worker_sessions` keyed by (work item, id) (`db._MIGRATIONS[54]`): the
rebuild keeps every row of every item, every column and the `rowid` order, and
then takes a second item's session with an id the first item's has."""

from support import schema
from test_db_migrations import _build_old_db

from kraft import db

#: Pre-upgrade session ids, 32 hex characters each, written out of id order.
_OLD = {
    "w1": ["f" * 32, "0" * 32],
    "w2": ["8cbfe6e27c1044b3e445c0f0d726357d"],
    "w3": ["a" * 32, "1" * 32, "e" * 32],
}


def _indexes(conn):
    """Each index on `worker_sessions` as (unique, origin, columns): the
    autoindex's name follows the table it was built for, not what it holds."""
    return {
        (
            r["unique"],
            r["origin"],
            tuple(c["name"] for c in conn.execute(f"PRAGMA index_info('{r['name']}')")),
        )
        for r in conn.execute("PRAGMA index_list(worker_sessions)")
    }


def test_the_session_key_migration_keeps_every_row_and_lets_items_share_an_id(tmp_path):
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 54)
    columns = [r["name"] for r in conn.execute("PRAGMA table_info(worker_sessions)")]
    for wid, sids in _OLD.items():
        schema.insert_item(conn, wid=wid)
        for sid in sids:
            schema.insert_session(conn, sid=sid, wid=wid, status="done")
    # Interleave the items and leave a gap, as a real table's history does.
    schema.insert_session(conn, sid="b" * 32, wid="w1", status="running")
    conn.execute("DELETE FROM worker_sessions WHERE id = ?", ("0" * 32,))
    filled = {
        c: (7 if t in ("INTEGER", "REAL") else f"v-{c}")
        for c, t in (
            (r["name"], r["type"]) for r in conn.execute("PRAGMA table_info(worker_sessions)")
        )
        if c not in ("id", "work_item_id", "status")
    }
    conn.execute(
        f"UPDATE worker_sessions SET {', '.join(f'{c} = ?' for c in filled)} WHERE rowid % 2 = 0",
        tuple(filled.values()),
    )
    conn.commit()
    before = [tuple(r) for r in conn.execute("SELECT rowid, * FROM worker_sessions ORDER BY rowid")]
    conn.close()

    conn = db._connect(path)
    db.migrate(conn)

    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 55
    after = [
        tuple(r)
        for r in conn.execute(
            f"SELECT rowid, {', '.join(columns)} FROM worker_sessions ORDER BY rowid"
        )
    ]
    assert after == before
    assert len({r[1] for r in after}) == 6
    pk = sorted(
        (r["pk"], r["name"]) for r in conn.execute("PRAGMA table_info(worker_sessions)") if r["pk"]
    )
    assert pk == [(1, "work_item_id"), (2, "id")]
    fresh = db._connect(tmp_path / "fresh.db")
    db.migrate(fresh)
    assert _indexes(conn) == _indexes(fresh)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    # Another item may now hold an id one item's session already has.
    schema.insert_session(conn, sid="8cbfe6e27c1044b3e445c0f0d726357d", wid="w1")
    schema.insert_session(conn, sid="abc123", wid="w1")
    schema.insert_session(conn, sid="abc123", wid="w2")
    rows = conn.execute(
        "SELECT work_item_id FROM worker_sessions WHERE id = 'abc123' ORDER BY work_item_id"
    ).fetchall()
    assert [r[0] for r in rows] == ["w1", "w2"]
