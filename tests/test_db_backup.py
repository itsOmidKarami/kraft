"""The copy `migrate` takes before it raises an old database's schema. A
sibling of test_db_migrations.py."""

import logging
import sqlite3
import stat

import pytest
from support import schema
from test_db_migrations import _build_old_db

from kraft import db


def _backups(tmp_path):
    return sorted(tmp_path.glob(f"orchestrator.db.pre-v{db.SCHEMA_VERSION}-*"))


def _old_db(tmp_path, version=44, ids=("w1", "w2")):
    """A database at `version` holding a work item per id, left open: its
    rows sit in the WAL, uncheckpointed, the way a running server leaves them."""
    conn = db._connect(tmp_path / "orchestrator.db")
    _build_old_db(conn, version)
    for wid in ids:
        schema.insert_item(conn, wid=wid)
    conn.commit()
    return conn


def _rows(path):
    old = sqlite3.connect(path)
    try:
        version = old.execute("PRAGMA user_version").fetchone()[0]
        return version, [r[0] for r in old.execute("SELECT id FROM work_items ORDER BY id")]
    finally:
        old.close()


def test_a_migration_backs_up_the_old_database_first(tmp_path, caplog):
    writer = _old_db(tmp_path)
    caplog.set_level(logging.WARNING, logger="kraft.db")

    conn = db._connect(tmp_path / "orchestrator.db")
    db.migrate(conn)

    assert schema.version(conn) == db.SCHEMA_VERSION
    [backup] = _backups(tmp_path)
    assert _rows(backup) == (44, ["w1", "w2"])
    # the 1.4 shape, not the migrated one: no column a later version added
    old = sqlite3.connect(backup)
    assert "stop_kind" not in {r[1] for r in old.execute("PRAGMA table_info(work_items)")}
    old.close()
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert not list(tmp_path.glob("*.partial"))
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
    assert _backups(tmp_path) == []


def test_a_second_upgrade_writes_a_fresh_copy(tmp_path, monkeypatch):
    """Upgrade, roll back by restoring the copy, work on, upgrade again: the
    second copy holds that work, and the first is left as it was -- even
    when both land in the same second."""
    monkeypatch.setattr(db.time, "strftime", lambda fmt: "20261002-120000")
    upgraded = tmp_path / "upgraded"
    upgraded.mkdir()
    writer = _old_db(upgraded, ids=("w1",))
    db.migrate(db._connect(upgraded / "orchestrator.db"))
    writer.close()
    [copy] = _backups(upgraded)
    # the rollback: the copy back in place, then more work on the old schema
    restored = tmp_path / "orchestrator.db"
    restored.write_bytes(copy.read_bytes())
    earlier = tmp_path / copy.name
    earlier.write_bytes(copy.read_bytes())
    conn = sqlite3.connect(restored)
    schema.insert_item(conn, wid="w2")
    conn.commit()
    conn.close()

    db.migrate(db._connect(restored))

    assert _rows(earlier) == (44, ["w1"])
    [second] = [p for p in _backups(tmp_path) if p != earlier]
    assert _rows(second) == (44, ["w1", "w2"])


def test_a_failed_copy_leaves_the_database_unmigrated(tmp_path, monkeypatch):
    writer = _old_db(tmp_path)

    def no_space(self, *a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(db.Path, "touch", no_space)
    conn = db._connect(tmp_path / "orchestrator.db")
    with pytest.raises(RuntimeError) as err:
        db.migrate(conn)
    message = str(err.value)
    assert f"orchestrator.db.pre-v{db.SCHEMA_VERSION}-" in message
    assert "The database is unchanged" in message
    assert "MB free beside it" in message
    assert schema.version(conn) == 44
    assert _backups(tmp_path) == []
    writer.close()
