import sqlite3

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
    assert table_count == 3


def test_migrate_rejects_newer_db(tmp_path):
    conn = db._connect(tmp_path / "orchestrator.db")
    conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
    try:
        db.migrate(conn)
        assert False, "expected RuntimeError"
    except RuntimeError:
        pass


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
        try:
            db.migrate(conn)
            assert False, "expected migration to fail"
        except sqlite3.OperationalError:
            pass

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
        assert tables == 3
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert user_version == db.SCHEMA_VERSION
    finally:
        db.SCHEMA_SQL = original_schema


def test_status_check_constraints(tmp_path):
    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO work_items (id, title, repo, chain_template, chain_definition, "
        "status, created_at, updated_at) VALUES ('w1', 't', '/r', 'quick-task', '{}', "
        "'active', 'now', 'now')"
    )
    try:
        conn.execute("UPDATE work_items SET status = 'bogus' WHERE id = 'w1'")
        assert False, "expected IntegrityError"
    except sqlite3.IntegrityError:
        pass


import asyncio


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
                lambda c: c.execute(
                    "SELECT title FROM work_items WHERE id = 'w1'"
                ).fetchone()
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

            try:
                await database.write(failing)
                assert False, "expected RuntimeError"
            except RuntimeError:
                pass

            count = database.read(
                lambda c: c.execute(
                    "SELECT count(*) FROM work_items WHERE id = 'w2'"
                ).fetchone()[0]
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
                    lambda c: c.execute(
                        "UPDATE work_items SET title = ? WHERE id = 'w'", (str(n),)
                    )
                )

            await asyncio.gather(*(bump(n) for n in range(20)))
            title = database.read(
                lambda c: c.execute(
                    "SELECT title FROM work_items WHERE id = 'w'"
                ).fetchone()["title"]
            )
            assert title == "19"
        finally:
            await database.close()

    asyncio.run(scenario())
