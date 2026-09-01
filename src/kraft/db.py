from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_STOP = object()

SCHEMA_VERSION = 1

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
"""


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
        raise RuntimeError(
            f"database schema v{version} is newer than code v{SCHEMA_VERSION}"
        )
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
    def __init__(self, writer: sqlite3.Connection, reader: sqlite3.Connection) -> None:
        self._writer = writer
        self._reader = reader
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task | None = None

    @classmethod
    async def open(cls, path: str | Path) -> "Database":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        writer = _connect(path)
        migrate(writer)
        reader = _connect(path)
        self = cls(writer, reader)
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
                    self._writer.rollback()
                    if not fut.done():
                        fut.set_exception(exc)
                else:
                    if not fut.done():
                        fut.set_result(result)
            finally:
                self._queue.task_done()

    async def write(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        await self._queue.put((fn, fut))
        return await fut

    def read(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        self._reader.rollback()  # drop any stale read snapshot so we see latest commits
        return fn(self._reader)

    async def close(self) -> None:
        await self._queue.put(_STOP)
        if self._task is not None:
            await self._task
        self._writer.close()
        self._reader.close()
