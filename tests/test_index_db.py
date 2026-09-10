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


def test_fresh_index_has_vector_tables(tmp_path):
    """4C: chunks + vec0 vectors land in the same file as FTS (04 §7)."""
    conn = index_db.open_index(tmp_path / "index.db")
    names = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")
    }
    assert {"document_chunks", "document_vectors"} <= names
    assert conn.execute("select vec_version()").fetchone()[0]
    assert conn.execute("PRAGMA user_version").fetchone()[0] == index_db.INDEX_SCHEMA_VERSION
    conn.close()


def test_v1_index_migrates_forward_keeping_documents(tmp_path):
    path = tmp_path / "index.db"
    conn = index_db.connect(path)
    schema = "\n".join(
        ln
        for ln in index_db.INDEX_SCHEMA_SQL.splitlines()
        # crude but sufficient: strip the 4C tables and the v3 `origin` column,
        # keep everything else -- what a genuine pre-4C, pre-origin file had.
        if "document_chunks" not in ln and "document_vectors" not in ln and "origin" not in ln
    )
    stmts = [s.strip() for s in schema.split(";") if s.strip()]
    conn.executescript(";\n".join(s for s in stmts if "chunk" not in s and "vec0" not in s) + ";")
    conn.execute("PRAGMA user_version = 1")
    conn.execute(
        "INSERT INTO documents (id, repo, source_kind, kind, title, path, content, "
        "content_hash, metadata_json, indexed_at) VALUES "
        "('d1','/r','artifact','specs','A','.engineering/specs/a.md','body','h','{}','t')"
    )
    conn.commit()
    conn.close()

    conn = index_db.open_index(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == index_db.INDEX_SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "document_chunks" in names
    conn.close()


def test_v2_index_migrates_forward_defaulting_origin_to_git_scan(tmp_path):
    """Kraft-<id>: `origin` distinguishes reconcile's git-truth rows from the
    ones ingested at gate approval, which must survive a rescan that finds no
    trace of them in git. An existing file predates the concept entirely --
    every row on it really did come from a git scan, so that is the default a
    plain `ADD COLUMN` gives them, not a guess."""
    path = tmp_path / "index.db"
    conn = index_db.connect(path)
    schema = "\n".join(ln for ln in index_db.INDEX_SCHEMA_SQL.splitlines() if "origin" not in ln)
    conn.executescript(schema)
    conn.execute("PRAGMA user_version = 2")
    conn.execute(
        "INSERT INTO documents (id, repo, source_kind, kind, title, path, content, "
        "content_hash, metadata_json, indexed_at) VALUES "
        "('d1','/r','artifact','specs','A','.engineering/specs/a.md','body','h','{}','t')"
    )
    conn.commit()
    conn.close()

    conn = index_db.open_index(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == index_db.INDEX_SCHEMA_VERSION
        row = conn.execute("SELECT origin FROM documents WHERE id='d1'").fetchone()
        assert row["origin"] == "git_scan"
    finally:
        conn.close()
