"""The copy `migrate` takes before it raises an old database's schema. A
sibling of test_db_migrations.py."""

import logging
import sqlite3
import stat

import pytest
from support import schema
from test_db_migrations import _build_old_db

from kraft import db


def _backup_path(tmp_path):
    return tmp_path / f"orchestrator.db.pre-v{db.SCHEMA_VERSION}"


def _old_db(tmp_path, version=44):
    """A database at `version` holding two work items, left open: its rows sit
    in the WAL, uncheckpointed, the way a running server leaves them."""
    conn = db._connect(tmp_path / "orchestrator.db")
    _build_old_db(conn, version)
    schema.insert_item(conn, wid="w1")
    schema.insert_item(conn, wid="w2", status="needs_human")
    conn.commit()
    return conn


def test_a_migration_backs_up_the_old_database_first(tmp_path, caplog):
    writer = _old_db(tmp_path)
    caplog.set_level(logging.WARNING, logger="kraft.db")

    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)

    assert schema.version(conn) == db.SCHEMA_VERSION
    backup = _backup_path(tmp_path)
    old = sqlite3.connect(backup)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 44
    assert old.execute("SELECT id, status FROM work_items ORDER BY id").fetchall() == [
        ("w1", "active"),
        ("w2", "needs_human"),
    ]
    # the 1.4 shape, not the migrated one: no column a later version added
    assert "stop_kind" not in {r[1] for r in old.execute("PRAGMA table_info(work_items)")}
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert not backup.with_name(backup.name + ".partial").exists()
    assert str(backup) in caplog.text
    writer.close()


@pytest.mark.parametrize("version", [0, db.SCHEMA_VERSION], ids=["fresh", "current"])
def test_no_migration_writes_no_backup(tmp_path, version):
    conn = db._connect(tmp_path / "orchestrator.db")
    if version:
        db.migrate(conn)
        conn.close()
        conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)
    assert schema.version(conn) == db.SCHEMA_VERSION
    assert sorted(p.name for p in tmp_path.iterdir() if ".pre-v" in p.name) == []


def test_an_existing_backup_is_kept(tmp_path, caplog):
    """A second upgrade from the same schema (after a rollback, say) leaves
    the first copy alone and still migrates."""
    backup = _backup_path(tmp_path)
    backup.write_bytes(b"the first upgrade's copy")
    writer = _old_db(tmp_path)
    caplog.set_level(logging.WARNING, logger="kraft.db")

    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)

    assert schema.version(conn) == db.SCHEMA_VERSION
    assert backup.read_bytes() == b"the first upgrade's copy"
    assert f"kept the existing backup {backup}" in caplog.text
    writer.close()
