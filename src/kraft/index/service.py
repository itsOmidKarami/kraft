from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
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

    def __init__(
        self, index_conn, state_db, *, repos_env: str | None = None, run_dirs=None
    ) -> None:
        self._conn = index_conn
        self._state = state_db
        self._run_dirs = run_dirs
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

    def _summary_path(self, ref: str, work_item_id: str, repo: str) -> Path | None:
        """Resolve a worker-reported ref. Untrusted input: absolute paths and
        anything escaping its base are refused (4B design §7)."""
        if not ref or Path(ref).is_absolute():
            return None
        bases = []
        if self._run_dirs is not None:
            bases.append(self._run_dirs.worktrees / work_item_id)
        bases.append(Path(repo))
        for base in bases:
            candidate = (base / ref).resolve()
            try:
                candidate.relative_to(base.resolve())
            except ValueError:
                continue
            if candidate.is_file():
                return candidate
        return None

    async def ingest_session_summary(self, session_id: str) -> bool:
        """Ingest the summary a finished worker session reported. Returns False
        (and logs) on anything unusable — the index never blocks the executor."""
        row = self._state.read(
            lambda c: c.execute(
                "SELECT ws.work_item_id, ws.node_id, ws.hook_point, "
                "       ws.session_summary_ref, wi.repo "
                "FROM worker_sessions ws JOIN work_items wi ON wi.id = ws.work_item_id "
                "WHERE ws.id = ?",
                (session_id,),
            ).fetchone()
        )
        if row is None or not row["session_summary_ref"]:
            return False
        ref = row["session_summary_ref"]
        path = self._summary_path(ref, row["work_item_id"], row["repo"])
        if path is None:
            logger.warning("session %s summary ref %r not readable; skipping", session_id, ref)
            return False
        try:
            text = path.read_text()
        except OSError:
            logger.warning("session %s summary %s unreadable; skipping", session_id, path)
            return False

        fm, body = ingest.split_front_matter(text)
        # DB wins for the session triple; front-matter contributes work items only.
        work_items = {row["work_item_id"]}
        for link in ingest.links_from_front_matter(fm):
            if link.work_item_id:
                work_items.add(link.work_item_id)
        links = [ingest.LinkRow(work_item_id=w) for w in sorted(work_items)]
        links.append(
            ingest.LinkRow(
                node_id=row["node_id"],
                hook_point=row["hook_point"],
                worker_session_id=session_id,
            )
        )
        doc = ingest.ScannedDoc(
            path=ref,
            kind=ingest.derive_kind(ref, fm),
            title=ingest.derive_title(ref, fm, body),
            content=body,
            content_hash=hashlib.sha256(body.encode()).hexdigest(),
            metadata={k: v for k, v in fm.items() if k not in ("title", "kind")},
            source_created_at=None,
            source_updated_at=None,
            source_kind="session_summary",
            links=tuple(links),
        )
        self._conn.execute("BEGIN")
        try:
            ingest.upsert_document(self._conn, row["repo"], doc, _now())
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise
        return True

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
                summaries: list[str] = []
                for ev in new:
                    self._cursor = ev["seq"]
                    payload = ev["payload"]
                    rescan = ev["type"] == "work_item_completed" or (
                        # 04 §2 piggyback: a work item starting on a repo is a
                        # good moment to refresh that repo's artifacts.
                        ev["type"] == "worker_session_started"
                        and payload.get("hook_point") == "on.env.prepare"
                    )
                    if rescan:
                        row = self._state.read(
                            lambda c, wid=ev["work_item_id"]: c.execute(
                                "SELECT repo FROM work_items WHERE id=?", (wid,)
                            ).fetchone()
                        )
                        if row is not None and row["repo"] not in repos:
                            repos.append(row["repo"])
                    elif ev["type"] == "worker_session_exited":
                        summaries.append(payload["session_id"])
                for sid in summaries:
                    try:
                        await self.ingest_session_summary(sid)
                    except Exception:  # noqa: BLE001
                        logger.exception("session summary ingest failed for %s", sid)
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

    _LINK_COLS = ("work_item_id", "node_id", "hook_point", "worker_session_id")

    def _links_for(self, doc_ids: list[str]) -> dict[str, list[dict]]:
        """All links for a set of documents in one query — never one per row."""
        out: dict[str, list[dict]] = {d: [] for d in doc_ids}
        if not doc_ids:
            return out
        placeholders = ",".join("?" * len(doc_ids))
        rows = self._conn.execute(
            f"SELECT document_id, {', '.join(self._LINK_COLS)} FROM document_links "
            f"WHERE document_id IN ({placeholders}) "
            "ORDER BY COALESCE(work_item_id, ''), COALESCE(worker_session_id, '')",
            doc_ids,
        ).fetchall()
        for r in rows:
            out[r["document_id"]].append({c: r[c] for c in self._LINK_COLS})
        return out

    def documents_for_work_item(self, work_item_id: str) -> list[dict]:
        """04 §9: what is linked to this work item. No content — the UI fetches
        that per document via GET /documents/{id}."""
        # The work-item rows and the node/hook/session row are siblings (04 §6),
        # so the triple has to be joined in from the document's session row
        # rather than read off the row that matched work_item_id.
        rows = self._conn.execute(
            "SELECT d.id AS document_id, d.repo, d.title, d.kind, d.source_kind, d.path, "
            "       s.node_id, s.hook_point, s.worker_session_id "
            "FROM document_links l "
            "JOIN documents d ON d.id = l.document_id "
            "LEFT JOIN document_links s ON s.document_id = l.document_id "
            "     AND s.worker_session_id IS NOT NULL "
            "WHERE l.work_item_id = ? ORDER BY d.path",
            (work_item_id,),
        ).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]

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
        try:
            rows = self._conn.execute("\n".join(sql), params).fetchall()
        except sqlite3.OperationalError:
            # Hyphens, dots and stray quotes are FTS5 syntax, not text. Retry with
            # every whitespace-separated term quoted as a literal phrase, which
            # keeps the implicit AND between terms but drops operator support —
            # acceptable, since the raw query already failed to parse (Kraft-bj9.4).
            params[0] = " ".join('"' + t.replace('"', '""') + '"' for t in q.split())
            rows = self._conn.execute("\n".join(sql), params).fetchall()
        links = self._links_for([r["id"] for r in rows])
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
                "links": links[r["id"]],
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
            "links": self._links_for([doc_id])[doc_id],
        }

    def health(self) -> dict:
        n = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        return {
            "last_scan_at": self._last_scan_at,
            "repos_scanned": self._repos_scanned,
            "documents": n,
            "errors": list(self._errors),
        }
