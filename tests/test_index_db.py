from __future__ import annotations

import sqlite3

from kraft.index import db as index_db
from kraft.paths import RunDirs


def _names(conn: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in conn.execute("SELECT name FROM sqlite_master").fetchall()}


def test_open_index_creates_schema_from_empty(tmp_path):
    conn = index_db.open_index(tmp_path / "index.db")
    try:
        names = _names(conn)
        assert {"documents", "document_links", "documents_fts"} <= names
        assert {"documents_ai", "documents_ad", "documents_au"} <= names
        (v,) = conn.execute("PRAGMA user_version").fetchone()
        assert v == index_db.INDEX_SCHEMA_VERSION
    finally:
        conn.close()


def test_open_index_is_idempotent(tmp_path):
    p = tmp_path / "index.db"
    index_db.open_index(p).close()
    conn = index_db.open_index(p)
    try:
        (v,) = conn.execute("PRAGMA user_version").fetchone()
        assert v == index_db.INDEX_SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    finally:
        conn.close()


def test_open_index_rebuilds_when_version_ahead(tmp_path):
    p = tmp_path / "index.db"
    conn = index_db.open_index(p)
    conn.execute(
        "INSERT INTO documents (id, repo, source_kind, kind, title, path, content, "
        "content_hash, metadata_json, indexed_at) VALUES "
        "('d1','/r','artifact','specs','T','.engineering/specs/a.md','body','h','{}','now')"
    )
    conn.commit()
    conn.execute(f"PRAGMA user_version = {index_db.INDEX_SCHEMA_VERSION + 5}")
    conn.commit()
    conn.close()

    conn = index_db.open_index(p)
    try:
        (v,) = conn.execute("PRAGMA user_version").fetchone()
        assert v == index_db.INDEX_SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    finally:
        conn.close()


def test_open_index_rebuilds_when_file_corrupt(tmp_path):
    p = tmp_path / "index.db"
    p.write_bytes(b"this is not a sqlite database at all, not even the header row")
    conn = index_db.open_index(p)
    try:
        assert "documents" in _names(conn)
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    finally:
        conn.close()


def test_rundirs_index_db_path(tmp_path):
    assert RunDirs(tmp_path).index_db == tmp_path / "index.db"
