import asyncio
import sqlite3

import pytest

from kraft import db


def _tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    return {r[0] for r in rows}


def test_migrations_keys_are_contiguous():
    """Two branches adding a migration under the same key merge as a silent
    last-write-wins dict literal, not a guaranteed git conflict -- nothing else
    catches a collision or a gap in the numbering (Kraft-cd47 hit this)."""
    keys = sorted(db._MIGRATIONS)
    assert keys == list(range(min(keys), db.SCHEMA_VERSION))


def test_waiting_is_an_allowed_work_item_status(tmp_path):
    """The row state a CI wait becomes (Kraft-ru98)."""
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1', 't', '/r', 'quick-task', '{}', "
        "'waiting', '2026-01-01', '2026-01-01')"
    )  # must not raise IntegrityError


def test_waiting_is_an_allowed_session_status(tmp_path):
    """`forge.run_task` closes its session with the handler's own status, so the
    sentinel has to be legal on both tables the way 'rate_limited' is."""
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1', 't', '/r', 'quick-task', '{}', "
        "'active', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, created_at) VALUES ('s1', 'w1', 'mr_checks', 'on.ci.poll', "
        "'/l', '/r.json', 'waiting', '2026-01-01')"
    )  # must not raise IntegrityError


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
    assert table_count == 6  # + auth_sessions (v6), work_item_repos (v18)


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
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        # Named, not a bare count: a bare number breaks silently (with no clue
        # which table changed) the next time a migration adds or renames one.
        assert tables == {
            "work_items",
            "events",
            "worker_sessions",
            "auth_sessions",
            "retry_counters",
            "work_item_repos",
        }
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
    if version < 18:
        skip_stmts = (*skip_stmts, "work_item_repos")
    if version < 7:
        drop_lines = (*drop_lines, "submodules", "root_merge_policy", "-- cross-repo")
    if version < 11:
        drop_lines = (*drop_lines, "bead_cwd")
    if version < 13:
        drop_lines = (*drop_lines, "description", "-- the brief this work item's")
    if version < 14:
        drop_lines = (
            *drop_lines,
            "branch           TEXT,",
            "-- the git branch this item's",
            "-- Computed once at intake",
            "-- NULL on items created before the column",
        )
    if version < 16:
        drop_lines = (
            *drop_lines,
            "implements_beads TEXT,",
            "-- sub-bead ids this item's description names",
            "-- from `description` at intake",
            "-- alongside `bead_id` on completion",
        )
    if version < 17:
        drop_lines = (*drop_lines, "retry_at         TEXT,", "-- set while status = 'rate_limited'")
    if version < 26:
        drop_lines = (
            *drop_lines,
            "archived_at      TEXT,",
            "archived_by      TEXT,",
            "-- who and when a completed/abandoned item was archived",
            '-- NULL means "not archived". Never set on any other status',
            "-- does not change `status`",
            "-- abandoned. 'you' | 'auto', enforced in kraft.store, not by a CHECK:",
            "-- the two writers are archive_work_item's only two callers.",
        )
    if version < 28:
        drop_lines = (
            *drop_lines,
            "ci_pipeline_ref  TEXT,",
            "-- GitLab pipeline pinned by the last `on.ci.poll` read",
        )
    if version < 29:
        drop_lines = (*drop_lines, "thread         INTEGER NOT NULL DEFAULT 1,")
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


def test_migrate_v25_to_v26_adds_archive_columns(tmp_path):
    """A v25 database migrates forward and gains archived_at/archived_by."""
    conn = db._connect(tmp_path / "orchestrator.db")
    _build_old_db(conn, 25)

    db.migrate(conn)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(work_items)").fetchall()}
    assert {"archived_at", "archived_by"} <= cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_migrate_v27_to_v28_adds_ci_pipeline_ref(tmp_path):
    """A v27 database migrates forward and gains work_items.ci_pipeline_ref."""
    conn = db._connect(tmp_path / "orchestrator.db")
    _build_old_db(conn, 27)

    db.migrate(conn)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(work_items)").fetchall()}
    assert "ci_pipeline_ref" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_migrate_v28_to_v29_adds_worker_sessions_thread(tmp_path):
    """A v28 database migrates forward and gains worker_sessions.thread,
    defaulted to 1 for every pre-existing row (Kraft-dkb6g)."""
    conn = db._connect(tmp_path / "orchestrator.db")
    _build_old_db(conn, 28)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, attempt, created_at) VALUES "
        "('s1','w1','implementation','escalation','l','r','done',1,'now')"
    )
    conn.commit()

    db.migrate(conn)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(worker_sessions)").fetchall()}
    assert "thread" in cols
    assert conn.execute("SELECT thread FROM worker_sessions WHERE id = 's1'").fetchone()[0] == 1
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


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
        drop_lines=(
            "pending_steer_context",
            "-- steer text",
            "                     ('active', 'needs_human', 'completed', 'paused', 'abandoned',",
            "                      'rate_limited', 'waiting')),",
        ),
        replace=(
            (
                "status           TEXT NOT NULL CHECK (status IN",
                "  status           TEXT NOT NULL CHECK (status IN "
                "('active', 'needs_human', 'completed')),",
            ),
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
    # Replaying from v7 re-applies every later step too, including v18's
    # CREATE TABLE work_item_repos -- drop it as well, or that step collides
    # with the table the earlier full migrate() already created.
    conn.execute("DROP TABLE work_item_repos")
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
            ("                    'waiting', 'conflict', 'infra', 'infra_stop')),", ""),
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
    # migration 14 (Kraft-kq8m) recomputes attempt from the real rows rather than
    # carrying the fixture's literal 2 forward: this is the only session in its
    # (work_item_id, node_id, hook_point), so the backfill numbers it 1.
    assert row["attempt"] == 1
    assert row["session_summary_ref"] == ".engineering/sessions/s1.md"
    assert row["created_at"] == "now"
    assert row["started_at"] == "started"
    assert row["round"] == 3
    # migration 29 (Kraft-s7c04.15) empties `model` rather than carry the
    # fixture's value forward: every value ever written to this column came from
    # `usage._model_of` reading the first `modelUsage` key, which is the haiku
    # warm-up. The measured columns beside it are untouched.
    assert row["model"] is None
    assert row["tokens_in"] == 10
    assert row["tokens_out"] == 20
    assert row["cost_usd"] == 0.5
    assert row["wall_ms"] == 1000
    assert row["exited_at"] == "exited"

    # both new statuses are now accepted by the rebuilt CHECK
    conn2.execute("UPDATE worker_sessions SET status = 'done_with_concerns' WHERE id = 's1'")
    conn2.execute("UPDATE worker_sessions SET status = 'needs_context' WHERE id = 's1'")


def test_migrate_v26_to_v27_widens_worker_sessions_status_for_ci_verdicts(tmp_path):
    """v26's worker_sessions CHECK has no 'conflict'/'infra'/'infra_stop' --
    `ci_poll`'s honest verdicts (Kraft-cbr, Kraft-bjjm, Kraft-h81i). SQLite
    cannot alter a constraint, so the table is rebuilt; every column has to
    survive it."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        26,
        replace=(
            (
                "                    'waiting', 'conflict', 'infra', 'infra_stop')),",
                "                    'waiting')),",
            ),
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
        "wall_ms, exited_at) VALUES ('s1', 'w1', 'mr_checks', 'on.ci.poll', 123, 456.7, "
        "'/l', '/r', 'waiting', 1, NULL, 'now', 'started', 0, NULL, NULL, NULL, NULL, "
        "NULL, NULL)"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE worker_sessions SET status = 'conflict' WHERE id = 's1'")
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION

    row = conn2.execute("SELECT * FROM worker_sessions WHERE id = 's1'").fetchone()
    assert row["work_item_id"] == "w1"
    assert row["node_id"] == "mr_checks"
    assert row["status"] == "waiting"

    conn2.execute("UPDATE worker_sessions SET status = 'conflict' WHERE id = 's1'")
    conn2.execute("UPDATE worker_sessions SET status = 'infra' WHERE id = 's1'")
    conn2.execute("UPDATE worker_sessions SET status = 'infra_stop' WHERE id = 's1'")

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


def test_migrate_v12_to_v13_adds_description(tmp_path):
    """The description migration adds the column via ALTER TABLE, and pre-existing
    rows survive with their other values intact and a NULL description."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 12, drop_lines=("description",))
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
    assert "description" in cols
    row = conn2.execute("SELECT title, description FROM work_items WHERE id='w1'").fetchone()
    assert row["title"] == "t"
    assert row["description"] is None


def test_migrate_v13_to_v14_adds_branch(tmp_path):
    """The branch migration adds the column via ALTER TABLE; a pre-existing row
    survives with a NULL branch, which is what keeps in-flight items on the
    `kraft/<id>` branch their worktree is already checked out on."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 13)
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
    assert "branch" in cols
    row = conn2.execute("SELECT title, branch FROM work_items WHERE id='w1'").fetchone()
    assert row["title"] == "t"
    assert row["branch"] is None


def test_migrate_v15_to_v16_adds_implements_beads(tmp_path):
    """The `implements_beads` migration adds the column via ALTER TABLE; a
    pre-existing row survives with it NULL, meaning "no sub-beads extracted"
    (Kraft-p8q1)."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 15)
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
    assert "implements_beads" in cols
    row = conn2.execute("SELECT title, implements_beads FROM work_items WHERE id='w1'").fetchone()
    assert row["title"] == "t"
    assert row["implements_beads"] is None


def test_migrate_v16_to_v17_drops_chain_template_not_null(tmp_path):
    """SQLite cannot alter a column constraint, so v16's `chain_template TEXT
    NOT NULL` is rebuilt the same 4-step way migration 11 was (Kraft-cd47).
    Existing rows keep their value; a new row may now write NULL. The rebuild
    also carries `implements_beads` (migration 15) forward, since v16 already
    has it."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        16,
        replace=(("chain_template   TEXT,", "chain_template   TEXT NOT NULL,"),),
    )
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
            "status, created_at, updated_at) VALUES ('w2','t','/r',NULL,'{}',"
            "'active','now','now')"
        )
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    # the pre-existing row survived the rebuild with its value intact
    row = conn2.execute("SELECT chain_template FROM work_items WHERE id='w1'").fetchone()
    assert row["chain_template"] == "quick-task"
    # and implements_beads (migration 15) survived the rebuild too
    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(work_items)")}
    assert "implements_beads" in cols
    # and the column now accepts NULL
    conn2.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w2','t','/r',NULL,'{}',"
        "'active','now','now')"
    )
    row2 = conn2.execute("SELECT chain_template FROM work_items WHERE id='w2'").fetchone()
    assert row2[0] is None


def test_fresh_schema_has_description(tmp_path):
    """SCHEMA_SQL and the migration path must agree — a fresh install and an
    upgraded one are the same database."""
    conn = db._connect(tmp_path / "fresh.db")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "description" in cols


def test_migration_19_adds_escalation_session_id_column(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    conn.execute("PRAGMA user_version = 19")
    conn.execute("ALTER TABLE work_items DROP COLUMN escalation_session_id")
    # Drop the columns every migration since v19 adds, so replaying those
    # migrations from a v19 snapshot doesn't collide with a fresh schema's
    # CREATE TABLE (which already carries every column current code knows).
    conn.execute("ALTER TABLE work_items DROP COLUMN auto_gate")
    conn.execute("ALTER TABLE work_items DROP COLUMN agent_overrides")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "escalation_session_id" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_migration_20_adds_auto_gate_column(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    conn.execute("PRAGMA user_version = 20")
    conn.execute("ALTER TABLE work_items DROP COLUMN auto_gate")
    # v20 replays v21 too, so its column has to come off the fresh schema as well.
    conn.execute("ALTER TABLE work_items DROP COLUMN agent_overrides")
    db.migrate(conn)
    row = conn.execute("SELECT auto_gate FROM work_items LIMIT 0").fetchone()
    assert row is None  # empty table; the column existing is what matters
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "auto_gate" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_migration_21_adds_agent_overrides_column(tmp_path):
    conn = db._connect(tmp_path / "s.db")
    db.migrate(conn)
    conn.execute("PRAGMA user_version = 21")
    conn.execute("ALTER TABLE work_items DROP COLUMN agent_overrides")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "agent_overrides" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_migration_14_backfills_worker_session_attempts(tmp_path):
    """Every row ever written carried the literal attempt = 1 (Kraft-kq8m), so
    fixing the INSERT alone leaves the observed items wrong for the life of the
    database. The backfill numbers each (work item, node, hook point) 1..N oldest
    first, with (created_at, id) breaking a shared timestamp."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 13)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    for sid, hook, created in [
        ("s1", "on.ci.poll", "2026-01-01T00:00:00"),
        ("s2", "on.ci.poll", "2026-01-01T00:00:01"),
        # s3 shares s2's timestamp: the id has to break the tie, or the two
        # collide on one number and the third is never used
        ("s3", "on.ci.poll", "2026-01-01T00:00:01"),
        ("s4", "on.mr.open", "2026-01-01T00:00:00"),
    ]:
        conn.execute(
            "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, "
            "log_path, result_path, status, attempt, created_at) "
            "VALUES (?, 'w1', 'mr_checks', ?, '/l', '/r', 'done', 1, ?)",
            (sid, hook, created),
        )
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    got = {r["id"]: r["attempt"] for r in conn2.execute("SELECT id, attempt FROM worker_sessions")}
    assert got == {"s1": 1, "s2": 2, "s3": 3, "s4": 1}


def test_migration_29_clears_the_unreliable_model_column(tmp_path):
    """Every `model` ever written came from `usage._model_of` reading the FIRST
    `modelUsage` key, which is Claude Code's haiku warm-up rather than the model
    that did the work (Kraft-s7c04.15). NULL already means "no model reported"
    to every reader, so the column is emptied rather than left asserting
    something false -- a cost-by-model comparison across this migration has to
    exclude NULL rows, and cannot if a wrong value looks like a right one."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(conn, 29)
    conn.execute("PRAGMA user_version = 29")
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, created_at, model, cost_usd) VALUES "
        "('s1','w1','verify','on.review.local.run','/l','/r','done','now',"
        "'claude-haiku-4-5-20251001', 1.25)"
    )
    conn.commit()
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    row = conn2.execute("SELECT model, cost_usd, status FROM worker_sessions").fetchone()
    assert row["model"] is None
    # only the model is unreliable; everything measured alongside it survives
    assert row["cost_usd"] == 1.25
    assert row["status"] == "done"


def test_migrate_v16_to_v17_rebuilds_for_rate_limited(tmp_path):
    """v16's CHECKs have no 'rate_limited' and work_items has no retry_at;
    SQLite cannot alter a constraint, so both tables are rebuilt the same
    12-step way migration 4 and 9 used."""
    path = tmp_path / "orchestrator.db"
    conn = db._connect(path)
    _build_old_db(
        conn,
        16,
        drop_lines=(
            "                      'rate_limited', 'waiting')),",
            "                    'waiting', 'conflict', 'infra', 'infra_stop')),",
        ),
        replace=(
            (
                "'paused', 'abandoned',",
                "                     ('active', 'needs_human', 'completed', 'paused', "
                "'abandoned')),",
            ),
            (
                "'done_with_concerns', 'needs_context', 'rate_limited', 'config_error',",
                "                    'done_with_concerns', 'needs_context')),",
            ),
        ),
    )
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'active','now','now')"
    )
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, created_at) VALUES ('s1', 'w1', 'verify', 'on.test.run', "
        "'/l', '/r', 'pending', 'now')"
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE work_items SET status = 'rate_limited' WHERE id = 'w1'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE worker_sessions SET status = 'rate_limited' WHERE id = 's1'")
    conn.close()

    conn2 = db._connect(path)
    db.migrate(conn2)
    assert conn2.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn2.execute("SELECT count(*) FROM work_items").fetchone()[0] == 1
    assert conn2.execute("SELECT count(*) FROM worker_sessions").fetchone()[0] == 1

    cols = {r["name"] for r in conn2.execute("PRAGMA table_info(work_items)")}
    assert "retry_at" in cols
    row = conn2.execute("SELECT retry_at FROM work_items WHERE id = 'w1'").fetchone()
    assert row["retry_at"] is None  # pre-existing row: NULL, not "not rate limited yet"

    conn2.execute("UPDATE work_items SET status = 'rate_limited', retry_at = 'x' WHERE id = 'w1'")
    conn2.execute("UPDATE worker_sessions SET status = 'rate_limited' WHERE id = 's1'")
    assert conn2.execute("PRAGMA foreign_key_check").fetchall() == []
    index_names = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "idx_worker_sessions_status" in index_names


def test_fresh_schema_has_retry_at(tmp_path):
    """SCHEMA_SQL and the migration path must agree — a fresh install and an
    upgraded one are the same database."""
    conn = db._connect(tmp_path / "fresh.db")
    db.migrate(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(work_items)")}
    assert "retry_at" in cols
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1','t','/r','quick-task','{}',"
        "'rate_limited','now','now')"
    )


def test_migrating_v13_adds_the_branch_column(tmp_path):
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    conn.execute("PRAGMA user_version = 13")
    conn.execute("ALTER TABLE work_items DROP COLUMN branch")
    # Replaying from v13 re-applies every later step too, including the
    # implements_beads ALTER (Kraft-p8q1) and v18's CREATE TABLE
    # work_item_repos -- drop both as well, or those steps collide with what
    # the earlier full migrate() already added.
    conn.execute("ALTER TABLE work_items DROP COLUMN implements_beads")
    conn.execute("DROP TABLE work_item_repos")

    db.migrate(conn)

    cols = {r[1] for r in conn.execute("PRAGMA table_info(work_items)").fetchall()}
    assert "branch" in cols
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_config_error_is_an_allowed_session_status(tmp_path):
    """The CHECK constraint has to know the status before the adapter can write
    it — otherwise a missing binary turns a bad-config stop into an
    IntegrityError three frames up (Kraft-579)."""
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1', 't', '/r', 'quick-task', '{}', "
        "'active', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO worker_sessions (id, work_item_id, node_id, hook_point, log_path, "
        "result_path, status, created_at) VALUES ('s1', 'w1', 'verify', 'on.test.run', "
        "'/l', '/r.json', 'config_error', '2026-01-01')"
    )  # must not raise IntegrityError


def test_worker_sessions_carries_a_head_sha(tmp_path):
    """Kraft-lu2's column rides along in this migration's table rebuild rather
    than paying for a second one."""
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(worker_sessions)").fetchall()}
    assert "head_sha" in cols
