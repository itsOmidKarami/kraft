import asyncio
import sqlite3

import pytest

from kraft import db


def _tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return {r[0] for r in rows}


def test_migrate_creates_schema_from_empty(tmp_path):
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)

    assert {"work_items", "events", "worker_sessions"} <= _tables(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    # events.seq is an autoincrement integer primary key
    cols = {r[1]: r for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    assert cols["seq"][5] == 1  # pk position 1

    index_names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_events_work_item", "idx_worker_sessions_status"} <= index_names
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_is_idempotent(tmp_path):
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    db.migrate(conn)
    db.migrate(conn)  # no-op, must not raise
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    table_count = conn2.execute(
        "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchone()[0]
    assert table_count == 5  # + auth_sessions (v6)


def test_migrate_rejects_newer_db(tmp_path):
    conn = db._connect(tmp_path / "orchestrator.db")
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError):
        db.migrate(conn)


def test_migrate_atomicity_rolls_back_on_error(tmp_path):
    """Verify that if migrate fails partway, no tables exist and user_version stays 0."""
    conn = db._connect(tmp_path / "orchestrator.db")

    # Simulate a failure mid-migration by corrupting SCHEMA_SQL
    original_schema = db.SCHEMA_SQL
    try:
        # Replace SCHEMA_SQL with schema that will fail partway (valid DDL then invalid DDL)
        db.SCHEMA_SQL = """
CREATE TABLE work_items (
  id TEXT PRIMARY KEY
);
INVALID SQL STATEMENT;
"""
        with pytest.raises(sqlite3.OperationalError):
            db.migrate(conn)

        # Verify rollback: no tables and user_version still 0
        tables = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        assert tables == 0, f"expected 0 tables after failed migration, got {tables}"
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_version == 0, f"expected user_version=0 after rollback, got {user_version}"

        # Verify recovery: restore schema and migrate succeeds
        db.SCHEMA_SQL = original_schema
        db.migrate(conn)
        tables = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        assert tables == 5
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_version == db.SCHEMA_VERSION
    finally:
        db.SCHEMA_SQL = original_schema


# The worker_sessions columns schema v4 added. Old-schema fixtures are built by
# subtraction from the current SCHEMA_SQL, so they have to name what to remove.
V4_COLS = (
    "-- usage capture",
    "started_at     TEXT,",
    "round          INTEGER",
    "model          TEXT",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "wall_ms",
)


def _build_old_db(conn, version, *, drop_lines=(), skip_stmts=(), replace=()):
    """Hand-build a pre-current schema: SCHEMA_SQL minus some lines/statements.

    `replace` is (needle, replacement-line) pairs for a line that has to change
    shape rather than disappear — a CHECK constraint that gained a value, say.
    """

    def rewrite(ln):
        for needle, new in replace:
            if needle in ln:
                return new
        return ln

    # columns and tables introduced after `version` were not there yet — decided
    # before the schema is built, or the drop never reaches it
    if version < 6:
        skip_stmts = (*skip_stmts, "auth_sessions")
    if version < 7:
        drop_lines = (*drop_lines, "submodules", "root_merge_policy", "-- cross-repo")
    if version < 11:
        drop_lines = (*drop_lines, "bead_cwd")
    schema = "\n".join(
        rewrite(ln) for ln in db.SCHEMA_SQL.splitlines() if not any(d in ln for d in drop_lines)
    )
    conn.execute("BEGIN")
    for stmt in (x.strip() for x in schema.split(";")):
        if stmt and not any(skip in stmt for skip in skip_stmts):
            conn.execute(stmt)
    conn.execute(f"PRAGMA user_version = {version}")
    conn.commit()


def test_migrate_creates_retry_counters(tmp_path):
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    assert "retry_counters" in _tables(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_migrate_v1_to_v2_adds_retry_counters(tmp_path):
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn, 1, drop_lines=("session_summary_ref", *V4_COLS), skip_stmts=("retry_counters",)
    )
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert "retry_counters" in _tables(conn2)
    assert conn2.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1  # data preserved


def test_bump_counter_inserts_then_increments(tmp_path):
    import asyncio as _a

    from kraft import policy, store

    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w','t','/r','quick-task','{}','active','now','now')"
                )
            )
            cap = policy.Cap(attempts=3, wall_clock_s=100)
            n1, s1, c1 = await database.write(
                lambda c: store.bump_counter(c, "w", "verify_fix_loop", cap)
            )
            assert n1 == 1
            assert c1 == cap  # insert returns the passed cap
            # a later bump with a DIFFERENT live cap must still return the snapshot
            n2, s2, c2 = await database.write(
                lambda c: store.bump_counter(
                    c, "w", "verify_fix_loop", policy.Cap(attempts=99, wall_clock_s=1)
                )
            )
            assert n2 == 2
            assert s2 == s1  # started_at frozen at first fire
            assert c2 == cap  # increment returns the row's snapshot, not attempts=99
            row = database.read(lambda c: store.read_counter(c, "w", "verify_fix_loop"))
            assert row["count"] == 2
            assert row["cap_attempts"] == 3 and row["cap_wall_s"] == 100
        finally:
            await database.close()

    _a.run(scenario())


def test_status_check_constraints(tmp_path):
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1', 't', '/r', 'quick-task', '{}', "
        "'active', 'now', 'now')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE work_items SET status = 'bogus' WHERE id = 'w1'")


def test_worker_sessions_status_check_constraint(tmp_path):
    """Nothing in test_status_check_constraints touches worker_sessions, so its
    CHECK could be dropped entirely and the suite would not notice."""
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1', 't', '/r', 'quick-task', '{}', "
        "'active', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, created_at) VALUES ('s1', 'w1', 'verify', 'on.test.run', "
        "'/l', '/r', 'pending', 'now')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE worker_sessions SET status = 'bogus' WHERE id = 's1'")
    # and the two new statuses this task adds are accepted
    conn.execute("UPDATE worker_sessions SET status = 'done_with_concerns' WHERE id = 's1'")
    conn.execute("UPDATE worker_sessions SET status = 'needs_context' WHERE id = 's1'")


def test_write_commits_on_success(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) "
                    "VALUES ('w1', 't', '/r', 'quick-task', '{}', 'active', 'now', 'now')"
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT title FROM work_items WHERE id = 'w1'").fetchone()
            )
            assert row["title"] == "t"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_write_rolls_back_on_exception(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:

            def failing(c):
                c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) "
                    "VALUES ('w2', 't', '/r', 'quick-task', '{}', 'active', 'now', 'now')"
                )
                raise RuntimeError("boom")

            with pytest.raises(RuntimeError):
                await database.write(failing)

            count = database.read(
                lambda c: c.execute("SELECT count(*) FROM work_items WHERE id = 'w2'").fetchone()[0]
            )
            assert count == 0
        finally:
            await database.close()

    asyncio.run(scenario())


def test_writes_are_serialized_in_order(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) "
                    "VALUES ('w', 't', '/r', 'quick-task', '{}', 'active', 'now', 'now')"
                )
            )

            async def bump(n):
                await database.write(
                    lambda c: c.execute("UPDATE work_items SET title = ? WHERE id = 'w'", (str(n),))
                )

            await asyncio.gather(*(bump(n) for n in range(20)))
            title = database.read(
                lambda c: c.execute("SELECT title FROM work_items WHERE id = 'w'").fetchone()[
                    "title"
                ]
            )
            assert title == "19"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_writer_survives_failing_fn_and_serves_next_write(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:

            def failing(c):
                raise RuntimeError("boom")

            with pytest.raises(RuntimeError):
                await database.write(failing)

            # writer task must still be alive and serving
            await database.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) "
                    "VALUES ('w1', 't', '/r', 'quick-task', '{}', 'active', 'now', 'now')"
                )
            )
            row = database.read(
                lambda c: c.execute("SELECT title FROM work_items WHERE id = 'w1'").fetchone()
            )
            assert row["title"] == "t"
        finally:
            await database.close()

    asyncio.run(scenario())


def test_write_after_close_raises_instead_of_hanging(tmp_path):
    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        await database.close()
        with pytest.raises(RuntimeError, match="writer is not running"):
            await asyncio.wait_for(database.write(lambda c: c.execute("SELECT 1")), timeout=2)

    asyncio.run(scenario())


def test_raising_rollback_still_informs_caller_and_next_db_works(tmp_path, monkeypatch):
    real_connect = sqlite3.connect

    class _BadRollback(sqlite3.Connection):
        def rollback(self):
            raise sqlite3.OperationalError("rollback failed")

    monkeypatch.setattr(
        db.sqlite3,
        "connect",
        lambda p, *a, **k: real_connect(p, *a, factory=_BadRollback, **k),
    )

    async def scenario():
        database = await db.Database.open(tmp_path / "orchestrator.db")
        try:

            def failing(c):
                raise RuntimeError("boom")

            # caller gets its own exception, not a hang
            with pytest.raises(RuntimeError, match="boom"):
                await asyncio.wait_for(database.write(failing), timeout=2)
        finally:
            # writer task died on the raising rollback; close re-raises it
            with pytest.raises(sqlite3.OperationalError):
                await database.close()

        # a fresh Database (normal connections) on the same file is functional
        monkeypatch.undo()
        fresh = await db.Database.open(tmp_path / "orchestrator.db")
        try:
            await fresh.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) "
                    "VALUES ('w1', 't', '/r', 'quick-task', '{}', 'active', 'now', 'now')"
                )
            )
            assert (
                fresh.read(lambda c: c.execute("SELECT count(*) FROM work_items").fetchone()[0])
                == 1
            )
        finally:
            await fresh.close()

    asyncio.run(scenario())


def test_migrate_v2_to_v3_adds_session_summary_ref(tmp_path):
    """A v2 database migrates forward and gains worker_sessions.session_summary_ref."""
    conn = db._connect(tmp_path / "orchestrator.db")
    _build_old_db(conn, 2, drop_lines=("session_summary_ref", *V4_COLS))

    db.migrate(conn)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(worker_sessions)").fetchall()}
    assert "session_summary_ref" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_migrate_mid_step_failure_rolls_back_whole_run(monkeypatch, tmp_path):
    """A v1 database has two steps to apply. If the second raises, the first must
    be rolled back too and user_version must stay where it started — the runner's
    BEGIN spans the whole range, not one step (Kraft-g5x)."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn, 1, drop_lines=("session_summary_ref", *V4_COLS), skip_stmts=("retry_counters",)
    )
    assert "retry_counters" not in _tables(conn)

    broken = dict(db._MIGRATIONS)
    broken[2] = ["INVALID SQL STATEMENT"]
    monkeypatch.setattr(db, "_MIGRATIONS", broken)

    with pytest.raises(sqlite3.OperationalError):
        db.migrate(conn)

    # step 1 (retry_counters) must not have survived the failure of step 2
    assert "retry_counters" not in _tables(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    # and the database is still migratable once the broken step is gone
    monkeypatch.undo()
    db.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert "retry_counters" in _tables(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(worker_sessions)").fetchall()}
    assert "session_summary_ref" in cols


def test_migrate_v4_to_v5_rebuilds_work_items_for_the_paused_status(tmp_path):
    """v4's status CHECK has no 'paused'; SQLite cannot alter a constraint, so the
    table is rebuilt. Rows and the events FK have to survive it."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        4,
        drop_lines=("pending_steer_context", "-- steer text"),
        replace=(
            (
                "status           TEXT NOT NULL CHECK (status IN",
                "  status           TEXT NOT NULL CHECK (status IN "
                "('active', 'needs_human', 'completed')),",
            ),
            ("('active', 'needs_human', 'completed', 'paused', 'abandoned')),", ""),
        ),
    )
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.execute(
        "INSERT INTO events (work_item_id, type, payload, created_at) "
        "VALUES ('w1','node_started','{}','now')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE work_items SET status = 'paused' WHERE id = 'w1'")
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn2.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1
    assert conn2.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    conn2.execute("UPDATE work_items SET status = 'paused' WHERE id = 'w1'")
    cols = {r[1] for r in conn2.execute("PRAGMA table_info(work_items)").fetchall()}
    assert "pending_steer_context" in cols
    # the events FK still points somewhere real after the drop/rename
    assert conn2.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_7_adds_attachments_column(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    conn.execute("PRAGMA user_version = 7")
    # Every column the 7 -> 11 steps add, or migrate() re-adds one that exists.
    conn.execute("ALTER TABLE work_items DROP COLUMN attachments")
    conn.execute("ALTER TABLE work_items DROP COLUMN base_ref")
    conn.execute("ALTER TABLE work_items DROP COLUMN bead_cwd")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "attachments" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_migrate_v8_to_v9_adds_base_ref(tmp_path):
    """The base_ref migration adds the column via ALTER TABLE, and pre-existing rows
    survive with their other values intact.

    Keyed v8 -> v9: origin/main's `attachments` migration took key 7 first, so
    base_ref renumbered to 8 when the two branches merged."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 8, drop_lines=("base_ref",))
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(work_items)")}
    assert "base_ref" in cols
    assert conn2.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1
    row = conn2.execute("SELECT id, title, status FROM work_items WHERE id='w1'").fetchone()
    assert row["id"] == "w1"
    assert row["title"] == "t"
    assert row["status"] == "active"


def test_migrate_v9_to_v10_widens_worker_sessions_status(tmp_path):
    """v9's worker_sessions CHECK has no 'done_with_concerns'/'needs_context'; SQLite
    cannot alter a constraint, so the table is rebuilt. Every column has to survive
    the rebuild, and the status index has to come back too."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        9,
        replace=(
            (
                "status         TEXT NOT NULL CHECK (status IN",
                "  status         TEXT NOT NULL CHECK (status IN "
                "('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown')),",
            ),
            ("'unknown',", ""),
            ("done_with_concerns", ""),
        ),
    )
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, pid, "
        "pid_start_time, log_path, result_path, status, attempt, session_summary_ref, "
        "created_at, started_at, round, model, tokens_in, tokens_out, cost_usd, "
        "wall_ms, exited_at) VALUES ('s1', 'w1', 'verify', 'on.test.run', 123, 456.7, "
        "'/l', '/r', 'unknown', 2, '.engineering/sessions/s1.md', 'now', 'started', "
        "3, 'claude', 10, 20, 0.5, 1000, 'exited')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE worker_sessions SET status = 'done_with_concerns' WHERE id = 's1'")
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION

    row = conn2.execute("SELECT * FROM worker_sessions WHERE id = 's1'").fetchone()
    assert row["work_item_id"] == "w1"
    assert row["node_id"] == "verify"
    assert row["hook_point"] == "on.test.run"
    assert row["pid"] == 123
    assert row["pid_start_time"] == 456.7
    assert row["log_path"] == "/l"
    assert row["result_path"] == "/r"
    assert row["status"] == "unknown"
    assert row["attempt"] == 2
    assert row["session_summary_ref"] == ".engineering/sessions/s1.md"
    assert row["created_at"] == "now"
    assert row["started_at"] == "started"
    assert row["round"] == 3
    assert row["model"] == "claude"
    assert row["tokens_in"] == 10
    assert row["tokens_out"] == 20
    assert row["cost_usd"] == 0.5
    assert row["wall_ms"] == 1000
    assert row["exited_at"] == "exited"

    # both new statuses are now accepted by the rebuilt CHECK
    conn2.execute("UPDATE worker_sessions SET status = 'done_with_concerns' WHERE id = 's1'")
    conn2.execute("UPDATE worker_sessions SET status = 'needs_context' WHERE id = 's1'")

    index_names = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_worker_sessions_status" in index_names


def test_migration_adds_base_ref(tmp_path):
    path = tmp_path / "m.db"
    conn = db._connect(path)
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "base_ref" in cols
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    assert version == db.SCHEMA_VERSION


def test_migrate_v10_to_v11_adds_bead_cwd(tmp_path):
    """An auto-intaken bead lives in its own repo's `.beads`, not the
    instance-wide `KRAFT_BD_CWD`, so closing it needs a per-item workspace
    (Kraft-8mu.5.2). Pre-existing rows survive with NULL, meaning KRAFT_BD_CWD."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 10)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(work_items)")}
    assert "bead_cwd" in cols
    row = conn2.execute("SELECT id, bead_cwd FROM work_items WHERE id='w1'").fetchone()
    assert row["bead_cwd"] is None
