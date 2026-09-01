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
    assert table_count == 3


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
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE work_items SET status = 'bogus' WHERE id = 'w1'")


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
