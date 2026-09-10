from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import sqlite_vec

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 3

INDEX_SCHEMA_SQL = """
CREATE TABLE documents (
  id                TEXT PRIMARY KEY,
  repo              TEXT NOT NULL,
  source_kind       TEXT NOT NULL CHECK (source_kind IN ('artifact', 'session_summary')),
  kind              TEXT,
  title             TEXT NOT NULL,
  path              TEXT NOT NULL,
  content           TEXT NOT NULL,
  content_hash      TEXT NOT NULL,
  metadata_json     TEXT NOT NULL DEFAULT '{}',
  source_created_at TEXT,
  source_updated_at TEXT,
  indexed_at        TEXT NOT NULL,
  -- Reconcile's provenance, not the user-facing category (that's
  -- source_kind): 'git_scan' rows are git-truth and get deleted the moment a
  -- rescan no longer finds them, while 'event_ingest' rows (session
  -- summaries, and now gate artifacts -- .engineering/ stays out of git) are
  -- written once by the executor/API and must never be treated as stale just
  -- because a git scan doesn't reproduce them.
  origin            TEXT NOT NULL DEFAULT 'git_scan' CHECK (origin IN ('git_scan', 'event_ingest')),
  UNIQUE (repo, path)
);

CREATE INDEX idx_documents_repo_kind ON documents(repo, source_kind, kind);

CREATE VIRTUAL TABLE documents_fts USING fts5 (
  title, content, content='documents', content_rowid='rowid'
);

CREATE TRIGGER documents_ai AFTER INSERT ON documents BEGIN
  INSERT INTO documents_fts (rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;

CREATE TRIGGER documents_ad AFTER DELETE ON documents BEGIN
  INSERT INTO documents_fts (documents_fts, rowid, title, content)
  VALUES ('delete', old.rowid, old.title, old.content);
END;

CREATE TRIGGER documents_au AFTER UPDATE ON documents BEGIN
  INSERT INTO documents_fts (documents_fts, rowid, title, content)
  VALUES ('delete', old.rowid, old.title, old.content);
  INSERT INTO documents_fts (rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;

CREATE TABLE document_links (
  id                TEXT PRIMARY KEY,
  document_id       TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  work_item_id      TEXT,
  node_id           TEXT,
  hook_point        TEXT,
  worker_session_id TEXT
);

CREATE INDEX idx_document_links_work_item ON document_links(work_item_id);
CREATE INDEX idx_document_links_document ON document_links(document_id);

CREATE TABLE document_chunks (
  id           TEXT PRIMARY KEY,
  document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index  INTEGER NOT NULL,
  chunk_text   TEXT NOT NULL,
  UNIQUE (document_id, chunk_index)
);

CREATE INDEX idx_document_chunks_document ON document_chunks(document_id);

-- vec0 carries no foreign keys, so rows here are deleted explicitly alongside
-- their chunks (04 §7 / 4C design §3). float[384] matches embed.DIMENSIONS; a
-- model with a different width means an INDEX_SCHEMA_VERSION bump and a rebuild.
CREATE VIRTUAL TABLE document_vectors USING vec0(
  chunk_id TEXT PRIMARY KEY,
  embedding float[384]
);
"""

# Additive DDL only; a data-losing reshape just bumps INDEX_SCHEMA_VERSION and
# lets open_index() rebuild the file (the index is disposable — 04 §1/§5).
_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE document_chunks (
  id           TEXT PRIMARY KEY,
  document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index  INTEGER NOT NULL,
  chunk_text   TEXT NOT NULL,
  UNIQUE (document_id, chunk_index)
)""",
        "CREATE INDEX idx_document_chunks_document ON document_chunks(document_id)",
        """CREATE VIRTUAL TABLE document_vectors USING vec0(
  chunk_id TEXT PRIMARY KEY,
  embedding float[384]
)""",
    ],
    2: [
        "ALTER TABLE documents ADD COLUMN origin TEXT NOT NULL DEFAULT 'git_scan' "
        "CHECK (origin IN ('git_scan', 'event_ingest'))",
    ],
}


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # vec0 DDL fails unless the extension is loaded on this very connection —
    # including inside _create_all on a brand new file.
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _create_all(conn: sqlite3.Connection) -> None:
    conn.executescript(INDEX_SCHEMA_SQL)
    conn.execute(f"PRAGMA user_version = {INDEX_SCHEMA_VERSION}")
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> bool:
    """True if the connection is usable at the current code version; False if
    the caller must discard the file and rebuild."""
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version == INDEX_SCHEMA_VERSION:
        return True
    if version > INDEX_SCHEMA_VERSION:
        logger.warning(
            "index db v%d newer than code v%d; rebuilding", version, INDEX_SCHEMA_VERSION
        )
        return False
    if version == 0:
        try:
            _create_all(conn)
            return True
        except sqlite3.DatabaseError:
            return False
    try:
        conn.execute("BEGIN")
        for v in range(version, INDEX_SCHEMA_VERSION):
            for stmt in _MIGRATIONS[v]:
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {v + 1}")
        conn.commit()
        return True
    except sqlite3.DatabaseError:
        conn.rollback()
        return False


def _wipe(path: Path) -> None:
    for p in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        p.unlink(missing_ok=True)


def open_index(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = connect(path)
        if _migrate(conn):
            return conn
        conn.close()
    except sqlite3.DatabaseError:
        logger.warning("index db at %s is not a usable database; rebuilding", path)
    _wipe(path)
    conn = connect(path)
    _create_all(conn)
    return conn
