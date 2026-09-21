"""Schema helpers for tests/test_db*.py: read a connection's shape back, and
write the minimal work item / session rows the schema tests need."""

from kraft import db


def tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return {r[0] for r in rows}


def columns(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def fresh(tmp_path):
    """A connection to a database `migrate` built from empty (SCHEMA_SQL)."""
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    return conn


def insert_item(conn, wid="w1", status="active", chain_definition="{}"):
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES (?, 't', '/r', 'quick-task', ?, ?, 'now', 'now')",
        (wid, chain_definition, status),
    )


def insert_session(conn, sid="s1", status="pending", wid="w1"):
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, created_at) VALUES (?, ?, 'verify', 'on.test.run', '/l', '/r', ?, "
        "'now')",
        (sid, wid, status),
    )
