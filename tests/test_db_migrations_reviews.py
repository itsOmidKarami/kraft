"""The migrations that added the review tables, and made a thread's gate nullable."""

from kraft import db


def test_migration_39_adds_the_review_tables(tmp_path):
    """v39 -> v40: the review-flow tables exist on an upgraded database."""
    path = tmp_path / "k.db"
    conn = db._connect(path)
    db.migrate(conn)  # fresh, at SCHEMA_VERSION
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"node_runs", "reviews", "review_threads", "review_comments"} <= names
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reviews)")}
    assert {"head_sha", "base_sha", "outcome", "summary", "gate"} <= cols


def test_migration_41_makes_review_gate_nullable_and_keeps_rows(tmp_path):
    """A mid-run thread has no gate; the rebuild keeps every existing row."""
    conn = db._connect(tmp_path / "k.db")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_definition, status, created_at, "
        "updated_at) VALUES ('w1', 't', '/r', '{}', 'active', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO review_threads (id, work_item_id, gate, anchor_sha, created_at) "
        "VALUES ('t1', 'w1', NULL, 'abc', 'now')"
    )
    conn.execute(
        "INSERT INTO reviews (id, work_item_id, gate, outcome, head_sha, base_sha, submitted_at) "
        "VALUES ('r1', 'w1', NULL, 'comment', 'abc', 'abc', 'now')"
    )
    conn.commit()
    assert conn.execute("SELECT gate FROM review_threads WHERE id='t1'").fetchone()[0] is None
