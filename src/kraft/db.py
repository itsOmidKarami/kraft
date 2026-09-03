from __future__ import annotations

import asyncio
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_STOP = object()

SCHEMA_VERSION = 2

SCHEMA_SQL = """
CREATE TABLE work_items (
  id               TEXT PRIMARY KEY,
  bead_id          TEXT,
  title            TEXT NOT NULL,
  repo             TEXT NOT NULL,
  chain_template   TEXT NOT NULL,
  chain_definition TEXT NOT NULL,
  current_node_id  TEXT,
  status           TEXT NOT NULL CHECK (status IN ('active', 'needs_human', 'completed')),
  created_at       TEXT NOT NULL,
  updated_at       TEXT NOT NULL
);

CREATE TABLE events (
  seq          INTEGER PRIMARY KEY AUTOINCREMENT,
  work_item_id TEXT NOT NULL REFERENCES work_items(id),
  type         TEXT NOT NULL,
  payload      TEXT NOT NULL,
  created_at   TEXT NOT NULL
);

CREATE INDEX idx_events_work_item ON events(work_item_id, seq);

CREATE TABLE worker_sessions (
  id             TEXT PRIMARY KEY,
  work_item_id   TEXT NOT NULL REFERENCES work_items(id),
  node_id        TEXT NOT NULL,
  hook_point     TEXT NOT NULL,
  pid            INTEGER,
  pid_start_time REAL,
  log_path       TEXT NOT NULL,
  result_path    TEXT NOT NULL,
  status         TEXT NOT NULL CHECK (status IN
                   ('pending', 'running', 'done', 'failed', 'capped_out', 'paused', 'unknown')),
  attempt        INTEGER NOT NULL DEFAULT 1,
  created_at     TEXT NOT NULL,
  exited_at      TEXT
);

CREATE INDEX idx_worker_sessions_status ON worker_sessions(status);

CREATE TABLE retry_counters (
  work_item_id  TEXT NOT NULL REFERENCES work_items(id),
  key           TEXT NOT NULL,
  count         INTEGER NOT NULL DEFAULT 0,
  cap_attempts  INTEGER NOT NULL,
  cap_wall_s    INTEGER NOT NULL,
  started_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (work_item_id, key)
);
"""

_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """CREATE TABLE retry_counters (
  work_item_id  TEXT NOT NULL REFERENCES work_items(id),
  key           TEXT NOT NULL,
  count         INTEGER NOT NULL DEFAULT 0,
  cap_attempts  INTEGER NOT NULL,
  cap_wall_s    INTEGER NOT NULL,
  started_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (work_item_id, key)
)"""
    ],
}


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    # PRAGMAs run as individual statements (not DML) so they don't open a txn.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    if version == SCHEMA_VERSION:
        return
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"database schema v{version} is newer than code v{SCHEMA_VERSION}")
    if version != 0:
        # Forward-only migration: apply each version step's statements, bump
        # user_version after each, all-or-nothing.
        try:
            conn.execute("BEGIN")
            for v in range(version, SCHEMA_VERSION):
                for stmt in _MIGRATIONS[v]:
                    conn.execute(stmt)
                conn.execute(f"PRAGMA user_version = {v + 1}")
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return
    # Explicit transaction: sqlite3 with isolation_level='' does NOT auto-open txns for DDL.
    # Must BEGIN explicitly to ensure all DDL + user_version bump commit atomically or not at all.
    try:
        conn.execute("BEGIN")
        # ponytail: naive ';' split — safe, the schema has no embedded semicolons
        for stmt in (s.strip() for s in SCHEMA_SQL.split(";")):
            if stmt:
                conn.execute(stmt)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


class Database:
    def __init__(
        self,
        writer: sqlite3.Connection,
        reader: sqlite3.Connection,
        *,
        on_commit: Callable[[], None] | None = None,
    ) -> None:
        self._writer = writer
        self._reader = reader
        self._on_commit = on_commit
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    def set_on_commit(self, cb: Callable[[], None] | None) -> None:
        self._on_commit = cb

    @classmethod
    async def open(
        cls, path: str | Path, *, on_commit: Callable[[], None] | None = None
    ) -> Database:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = _connect(path)
        migrate(writer)
        reader = _connect(path)
        self = cls(writer, reader, on_commit=on_commit)
        self._task = asyncio.create_task(self._run())
        return self

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is _STOP:
                    return
                fn, fut = item
                try:
                    result = fn(self._writer)
                    self._writer.commit()
                except BaseException as exc:  # noqa: BLE001 - re-raised to caller
                    # Inform the caller FIRST: if rollback() itself raises, the
                    # exception escaping _run kills the writer task and wedges
                    # every pending/future write(). A failed rollback still
                    # propagates (dirty txn must not be silently committed by
                    # the next write), but only after the caller has its result.
                    if not fut.done():
                        fut.set_exception(exc)
                    self._writer.rollback()
                else:
                    if not fut.done():
                        fut.set_result(result)
                    if self._on_commit is not None:
                        try:
                            self._on_commit()
                        except Exception:
                            logger.exception("on_commit listener raised")
            finally:
                self._queue.task_done()

    async def write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        if self._task is None or self._task.done():
            raise RuntimeError("db writer is not running")
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put((fn, fut))
        return await fut

    def read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        # SELECT-only work leaves no Python-level txn to roll back; the read
        # snapshot is actually released when CPython finalizes the temp cursor.
        # Keep this call anyway: harmless, and correct if a read fn does DML.
        self._reader.rollback()
        return fn(self._reader)

    async def close(self) -> None:
        await self._queue.put(_STOP)
        if self._task is not None:
            await self._task
        self._writer.close()
        self._reader.close()
