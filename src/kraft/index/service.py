from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from kraft import events
from kraft.events import _now
from kraft.index import ingest

logger = logging.getLogger(__name__)

_SCAN_FANOUT = 4


class Indexer:
    """Owns the index connection. Runs one startup scan, answers search /
    get_document, and (once `start()`ed) drains the state event bus to trigger
    targeted repo rescans on `work_item_completed`."""

    def __init__(self, index_conn, state_db, *, repos_env: str | None = None) -> None:
        self._conn = index_conn
        self._state = state_db
        self._repos_env = (
            repos_env if repos_env is not None else os.environ.get("KRAFT_INDEX_REPOS")
        )
        self._last_scan_at: str | None = None
        self._repos_scanned: int = 0
        self._errors: list[str] = []
        self._cursor: int = 0
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ---- repo discovery ----

    def repos(self) -> list[str]:
        seen = {
            r["repo"]
            for r in self._state.read(
                lambda c: c.execute("SELECT DISTINCT repo FROM work_items").fetchall()
            )
        }
        if self._repos_env:
            seen.update(p for p in self._repos_env.split(os.pathsep) if p)
        return sorted(p for p in seen if Path(p).is_dir())

    # ---- ingestion ----

    async def rescan_repo(self, repo: str) -> ingest.ReconcileStats:
        scanned = await asyncio.to_thread(ingest.scan_repo, Path(repo))
        # ponytail: reconcile writes run on the event loop against the single
        # index connection. Fine while ingestion is small and low-QPS; move
        # behind a writer queue if a large-repo scan ever stalls the loop.
        return ingest.reconcile(self._conn, repo, scanned)

    async def rescan_all(self) -> dict[str, ingest.ReconcileStats]:
        repos = self.repos()
        out: dict[str, ingest.ReconcileStats] = {}
        sem = asyncio.Semaphore(_SCAN_FANOUT)

        async def one(r: str) -> None:
            async with sem:
                try:
                    out[r] = await self.rescan_repo(r)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("index rescan failed for %s", r)
                    self._errors.append(f"{r}: {exc!r}")

        await asyncio.gather(*(one(r) for r in repos))
        self._last_scan_at = _now()
        self._repos_scanned = len(repos)
        return out

    async def startup_scan(self) -> dict[str, ingest.ReconcileStats]:
        self._errors = []
        return await self.rescan_all()

    # ---- live event drain ----

    def notify(self) -> None:
        self._wakeup.set()

    async def start(self) -> None:
        self._cursor = self._state.read(
            lambda c: c.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        )
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self._wakeup.wait()
                self._wakeup.clear()
                new = self._state.read(lambda c: events.read_after(c, self._cursor))
                repos: list[str] = []
                for ev in new:
                    self._cursor = ev["seq"]
                    if ev["type"] != "work_item_completed":
                        continue
                    row = self._state.read(
                        lambda c, wid=ev["work_item_id"]: c.execute(
                            "SELECT repo FROM work_items WHERE id=?", (wid,)
                        ).fetchone()
                    )
                    if row is not None and row["repo"] not in repos:
                        repos.append(row["repo"])
                for repo in repos:
                    try:
                        await self.rescan_repo(repo)
                    except Exception:  # noqa: BLE001
                        logger.exception("triggered rescan failed for %s", repo)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("indexer drain iteration failed")

    # ---- queries ----

    def search(
        self,
        q: str,
        *,
        source_kind: str | None = None,
        kind: str | None = None,
        repo: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        sql = [
            "SELECT d.id, d.repo, d.kind, d.source_kind, d.title, d.path,",
            "  snippet(documents_fts, 1, '[', ']', '…', 64) AS snippet,",
            "  bm25(documents_fts) AS score",
            "FROM documents_fts JOIN documents d ON d.rowid = documents_fts.rowid",
            "WHERE documents_fts MATCH ?",
        ]
        params: list = [q]
        if source_kind:
            sql.append("AND d.source_kind = ?")
            params.append(source_kind)
        if kind:
            sql.append("AND d.kind = ?")
            params.append(kind)
        if repo:
            sql.append("AND d.repo = ?")
            params.append(repo)
        sql.append("ORDER BY score LIMIT ?")
        params.append(limit)
        rows = self._conn.execute("\n".join(sql), params).fetchall()
        return [
            {
                "id": r["id"],
                "repo": r["repo"],
                "kind": r["kind"],
                "source_kind": r["source_kind"],
                "title": r["title"],
                "path": r["path"],
                "snippet": r["snippet"],
                "score": r["score"],
                "links": [],
            }
            for r in rows
        ]

    def get_document(self, doc_id: str) -> dict | None:
        r = self._conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if r is None:
            return None
        return {
            "id": r["id"],
            "repo": r["repo"],
            "source_kind": r["source_kind"],
            "kind": r["kind"],
            "title": r["title"],
            "path": r["path"],
            "content": r["content"],
            "metadata": json.loads(r["metadata_json"]),
            "source_created_at": r["source_created_at"],
            "source_updated_at": r["source_updated_at"],
            "indexed_at": r["indexed_at"],
            "links": [],
        }

    def health(self) -> dict:
        n = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        return {
            "last_scan_at": self._last_scan_at,
            "repos_scanned": self._repos_scanned,
            "documents": n,
            "errors": list(self._errors),
        }
