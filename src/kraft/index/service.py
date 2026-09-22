from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
from pathlib import Path

from kraft import config as config_mod
from kraft import events
from kraft.events import _now
from kraft.index import ingest
from kraft.index.embed import Embedder

logger = logging.getLogger(__name__)

_SCAN_FANOUT = 4

# Reciprocal rank fusion. BM25 is unbounded and negative, cosine distance is
# 0..2 — blending those scores directly needs normalisation that is fragile at
# small corpus sizes. RRF only needs the ranks. k=60 is the value from the
# original paper and 04 §10's placeholder weight (equal) is simply no weight.
RRF_K = 60


def rrf(*ranked_lists: list[str], k: int = RRF_K) -> dict[str, float]:
    """Fuse ranked id lists into {id: score}, higher is better."""
    scores: dict[str, float] = {}
    for ids in ranked_lists:
        for rank, doc_id in enumerate(ids, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


class Indexer:
    """Owns the index connection. Runs one startup scan, answers search /
    get_document, and (once `start()`ed) drains the state event bus to trigger
    targeted repo rescans on `work_item_completed`."""

    def __init__(
        self,
        index_conn,
        state_db,
        *,
        repos_env: str | None = None,
        run_dirs=None,
        repos_path: Path | None = None,
    ) -> None:
        self._conn = index_conn
        self._state = state_db
        self._run_dirs = run_dirs
        self._repos_path = repos_path
        self._repos_env = (
            repos_env if repos_env is not None else os.environ.get("KRAFT_INDEX_REPOS")
        )
        self._embedder = Embedder()
        self._last_scan_at: str | None = None
        self._repos_scanned: int = 0
        self._errors: list[str] = []
        self._cursor: int = 0
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None
        # Every write to `self._conn` goes through this: `reconcile` now awaits
        # mid-transaction (the embedding step runs in a thread), so two writers
        # in flight at once — rescan_all's fan-out, a live ingest_session_summary
        # — could otherwise interleave their BEGIN..COMMIT on the one connection.
        # Reads (search, get_document) are unaffected; only writers serialize.
        self._write_lock = asyncio.Lock()

    # ---- repo discovery ----

    def repos(self) -> list[str]:
        # Resolved before landing in the set: `KRAFT_INDEX_REPOS` (an e2e-only
        # bootstrap path) and `repos.yaml`/`work_items.repo` (canonicalized at
        # connect time via `probe_repo`'s `git rev-parse`) can name the same
        # directory with different strings — a symlinked tmp root (macOS's
        # `/var` -> `/private/var`) is the case that actually happens. Two
        # strings for one repo means two scans and two `documents` rows for
        # the same file (Kraft-k7uq).
        seen = {
            str(Path(r["repo"]).resolve())
            for r in self._state.read(
                lambda c: c.execute("SELECT DISTINCT repo FROM work_items").fetchall()
            )
        }
        if self._repos_env:
            seen.update(str(Path(p).resolve()) for p in self._repos_env.split(os.pathsep) if p)
        seen.update(str(Path(p).resolve()) for p in self._connected_repos())
        return sorted(p for p in seen if Path(p).is_dir())

    def _connected_repos(self) -> list[str]:
        """Repos connected through Settings (`repos.yaml`), read live because
        that file is edited by the Settings screens while Kraft runs.

        Degrades like `kraft.api.deps.launch` rather than raising: a malformed file must
        not take down a scan of the repos that are still fine. Steering is not
        validated here — indexing has nothing to do with steering files.
        """
        if self._repos_path is None:
            return []
        try:
            repos = config_mod.load_repos(self._repos_path)
        except (config_mod.ConfigError, OSError) as exc:
            logger.warning("repo config unreadable, indexing without it: %s", exc)
            return []
        return [r.path for r in repos]

    # ---- ingestion ----

    async def rescan_repo(self, repo: str) -> ingest.ReconcileStats:
        scanned = await asyncio.to_thread(ingest.scan_repo, Path(repo))
        # The embedding step inside `reconcile` runs in a thread (Kraft-fix:
        # a big first scan used to peg the loop for minutes and take /health
        # and everything else down with it). `_write_lock` keeps that from
        # becoming two writers interleaving on the one index connection.
        async with self._write_lock:
            return await ingest.reconcile(self._conn, repo, scanned, embedder=self._embedder)

    async def purge_repo(self, repo: str) -> ingest.ReconcileStats:
        """Drop every indexed document for `repo` — a disconnected repo must
        stop answering searches. Not `reconcile` against an empty scan any
        more: `reconcile` now only ever deletes `origin='git_scan'` rows (the
        fix that keeps a rescan from deleting a gate artifact it will never
        find in git), so that path alone would leave every gate artifact and
        session summary behind after disconnect. `purge_all` drops the lot."""
        async with self._write_lock:
            return await ingest.purge_all(self._conn, repo)

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
            origin="event_ingest",
            links=tuple(links),
        )
        async with self._write_lock:
            self._conn.execute("BEGIN")
            try:
                await ingest.upsert_document(self._conn, row["repo"], doc, _now(), self._embedder)
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
        return True

    async def ingest_gate_artifact(
        self,
        *,
        repo: str,
        work_item_id: str,
        path: str,
        content: str,
        node_id: str | None = None,
        hook_point: str | None = None,
    ) -> None:
        """Persist an approved gate artifact (spec/plan/chain_review/
        review_brief) the way a session summary is already persisted: read
        straight off the worktree by the caller, upserted here, no git scan
        involved.

        This is that artifact's only durable copy from here on: `.engineering/`
        never lands in the connected repo's git history
        (`forge._work_product_pathspec`), so once the worktree that holds it is
        gone, this row is all there is.
        The caller (`kraft.api.routes.gates.approve_gate`) is what makes "gate approval" the right
        moment -- it is the first point the content is accepted rather than
        still being drafted, and reuses the same worktree-containment-checked
        read as the reviewer-facing endpoint.
        """
        fm, body = ingest.split_front_matter(content)
        # Front-matter wins nothing here that the caller didn't already decide;
        # it only ever contributes extra linked work items, same as a summary.
        work_items = {work_item_id}
        for link in ingest.links_from_front_matter(fm):
            if link.work_item_id:
                work_items.add(link.work_item_id)
        links = [ingest.LinkRow(work_item_id=w) for w in sorted(work_items)]
        if node_id or hook_point:
            links.append(ingest.LinkRow(node_id=node_id, hook_point=hook_point))
        doc = ingest.ScannedDoc(
            path=path,
            kind=ingest.derive_kind(path, fm),
            title=ingest.derive_title(path, fm, body),
            content=body,
            content_hash=hashlib.sha256(body.encode()).hexdigest(),
            metadata={k: v for k, v in fm.items() if k not in ("title", "kind")},
            source_created_at=None,
            source_updated_at=None,
            source_kind="artifact",
            origin="event_ingest",
            links=tuple(links),
        )
        async with self._write_lock:
            self._conn.execute("BEGIN")
            try:
                await ingest.upsert_document(self._conn, repo, doc, _now(), self._embedder)
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise

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
                    # 04 §2 piggyback: a work item starting on a repo is a good
                    # moment to refresh that repo's artifacts. `chain_loaded`,
                    # not the old `on.env.prepare` session: V1 has no env_setup
                    # node to piggyback on (its work is implicit runtime
                    # preparation now), and `store.load_chain` emits this at the
                    # top of the walk with the same meaning.
                    rescan = ev["type"] in ("work_item_completed", "chain_loaded")
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
        """04 §9: what is linked to this work item, newest-indexed first. No
        content — the UI fetches that per document via GET /documents/{id}."""
        # The work-item rows and the node/hook/session row are siblings (04 §6),
        # so the triple has to be joined in from the document's session row
        # rather than read off the row that matched work_item_id.
        rows = self._conn.execute(
            "SELECT d.id AS document_id, d.repo, d.title, d.kind, d.source_kind, d.path, "
            "       d.indexed_at, s.node_id, s.hook_point, s.worker_session_id "
            "FROM document_links l "
            "JOIN documents d ON d.id = l.document_id "
            "LEFT JOIN document_links s ON s.document_id = l.document_id "
            "     AND s.worker_session_id IS NOT NULL "
            "WHERE l.work_item_id = ?",
            (work_item_id,),
        ).fetchall()
        docs = [{**{k: r[k] for k in r.keys()}, "attachment_kind": None} for r in rows]
        seen = {d["document_id"] for d in docs}
        docs += [d for d in self._attachment_docs(work_item_id) if d["document_id"] not in seen]
        # W13 A: the run a session summary came from. worker_sessions lives in
        # the state db, not the index, so the join is a keyed lookup rather
        # than SQL. Artifacts and attachments have no session: all three null.
        runs = self._session_runs(
            [d["worker_session_id"] for d in docs if d.get("worker_session_id")]
        )
        for d in docs:
            run = runs.get(d.get("worker_session_id"))
            d["attempt"] = run["attempt"] if run else None
            d["round"] = run["round"] if run else None
            d["session_status"] = run["status"] if run else None
        # source_created_at/updated_at aren't populated for every document kind
        # (session summaries never carry them); indexed_at is the one time field
        # every row has, so it's what "sorted by time" sorts on.
        docs.sort(key=lambda d: d["indexed_at"], reverse=True)
        return docs

    def _session_runs(self, session_ids: list[str]) -> dict[str, dict]:
        """attempt/round/status per worker session id, for the ids given."""
        if not session_ids:
            return {}
        marks = ",".join("?" * len(session_ids))
        rows = self._state.read(
            lambda c: c.execute(
                f"SELECT id, attempt, round, status FROM worker_sessions WHERE id IN ({marks})",
                session_ids,
            ).fetchall()
        )
        return {
            r["id"]: {"attempt": r["attempt"], "round": r["round"], "status": r["status"]}
            for r in rows
        }

    def _attachment_docs(self, work_item_id: str) -> list[dict]:
        """Intake attachments (Kraft-dgh), joined at read time rather than stored
        as document_links: `upsert_document` rewrites a document's links whole on
        every rescan, so a stored row would not survive one."""
        row = self._state.read(
            lambda c: c.execute(
                "SELECT repo, attachments FROM work_items WHERE id = ?", (work_item_id,)
            ).fetchone()
        )
        if row is None or not row["attachments"]:
            return []
        out = []
        for attachment in json.loads(row["attachments"]):
            doc = self._conn.execute(
                "SELECT id AS document_id, repo, title, kind, source_kind, path, indexed_at "
                "FROM documents WHERE repo = ? AND path = ?",
                (row["repo"], attachment["path"]),
            ).fetchone()
            if doc is None:
                # Not indexed yet: scan_repo lists via `git ls-files` against the
                # registered main checkout, so a committed-but-unmerged attachment
                # doesn't show up there. Read it live from the item's own worktree
                # instead — same fallback `_summary_path` already does above.
                synthesized = self._synthesize_attachment_doc(work_item_id, row["repo"], attachment)
                if synthesized is None:
                    continue
                out.append(synthesized)
                continue
            out.append(
                {
                    **{k: doc[k] for k in doc.keys()},
                    "node_id": None,
                    "hook_point": None,
                    "worker_session_id": None,
                    "attachment_kind": attachment["kind"],
                }
            )
        return out

    def _synthesize_attachment_doc(
        self, work_item_id: str, repo: str, attachment: dict
    ) -> dict | None:
        """Read an unindexed attachment straight from the item's own worktree
        and shape it like a `documents` row, so an attach-based item shows its
        spec/plan before the branch that carries them ever merges."""
        path = self._summary_path(attachment["path"], work_item_id, repo)
        if path is None:
            return None
        try:
            text = path.read_text()
        except OSError:
            return None
        fm, body = ingest.split_front_matter(text)
        return {
            "document_id": f"attachment:{work_item_id}:{attachment['kind']}",
            "repo": repo,
            "title": ingest.derive_title(attachment["path"], fm, body),
            "kind": ingest.derive_kind(attachment["path"], fm),
            "source_kind": ingest.source_kind_for(attachment["path"]),
            "path": attachment["path"],
            "indexed_at": _now(),
            "node_id": None,
            "hook_point": None,
            "worker_session_id": None,
            "attachment_kind": attachment["kind"],
        }

    def _filter_sql(self, source_kind, kind, repo) -> tuple[str, list]:
        clauses, params = [], []
        if source_kind:
            clauses.append("AND d.source_kind = ?")
            params.append(source_kind)
        if kind:
            clauses.append("AND d.kind = ?")
            params.append(kind)
        if repo:
            clauses.append("AND d.repo = ?")
            params.append(repo)
        return " ".join(clauses), params

    def _vector_ids(
        self, q: str, *, source_kind=None, kind=None, repo=None, limit: int = 20
    ) -> tuple[list[str], dict[str, str]]:
        """Document ids by nearest chunk, plus each document's best chunk text
        (used as the snippet, since a vector hit has no FTS snippet)."""
        vectors = self._embedder.encode([q])
        if not vectors:
            return [], {}
        blob = ingest._serialize(vectors[0])
        where, params = self._filter_sql(source_kind, kind, repo)
        # Over-fetch chunks: several may belong to one document, and the filters
        # are applied after the KNN, so ask for more than `limit` documents.
        rows = self._conn.execute(
            "SELECT c.document_id AS id, c.chunk_text, v.distance "
            "FROM document_vectors v "
            "JOIN document_chunks c ON c.id = v.chunk_id "
            "JOIN documents d ON d.id = c.document_id "
            f"WHERE v.embedding MATCH ? AND k = ? {where} "
            "ORDER BY v.distance",
            [blob, max(limit * 5, 50), *params],
        ).fetchall()
        ordered: list[str] = []
        snippets: dict[str, str] = {}
        for r in rows:
            if r["id"] in snippets:
                continue
            ordered.append(r["id"])
            snippets[r["id"]] = r["chunk_text"][:200]
            if len(ordered) >= limit:
                break
        return ordered, snippets

    def search(
        self,
        q: str,
        *,
        source_kind: str | None = None,
        kind: str | None = None,
        repo: str | None = None,
        limit: int = 20,
        mode: str = "fts",
    ) -> list[dict]:
        return self.search_with_mode(
            q, source_kind=source_kind, kind=kind, repo=repo, limit=limit, mode=mode
        )[0]

    def search_with_mode(
        self,
        q: str,
        *,
        source_kind: str | None = None,
        kind: str | None = None,
        repo: str | None = None,
        limit: int = 20,
        mode: str = "hybrid",
    ) -> tuple[list[dict], str]:
        """Returns (results, mode actually served).

        An explicit vector request with no embedder raises: answering it with
        text ranking would misrepresent what was searched. A hybrid request
        degrades to fts and says so (4C design §2).
        """
        if mode not in ("fts", "vector", "hybrid"):
            raise ValueError(f"unknown mode: {mode}")
        has_embedder = self._embedder.available()
        if mode == "vector" and not has_embedder:
            raise RuntimeError(self._embedder.reason or "embeddings unavailable")
        if mode == "hybrid" and not has_embedder:
            mode = "fts"

        filters = {"source_kind": source_kind, "kind": kind, "repo": repo}
        if mode == "fts":
            return self._fts(q, limit=limit, **filters), "fts"

        vec_ids, snippets = self._vector_ids(q, limit=limit, **filters)
        if mode == "vector":
            return self._hydrate(vec_ids, snippets), "vector"

        fts_rows = self._fts(q, limit=limit, **filters)
        fused = rrf([r["id"] for r in fts_rows], vec_ids)
        by_id = {r["id"]: r for r in fts_rows}
        ordered = sorted(fused, key=lambda i: -fused[i])[:limit]
        missing = [i for i in ordered if i not in by_id]
        by_id.update({r["id"]: r for r in self._hydrate(missing, snippets)})
        return [by_id[i] for i in ordered if i in by_id], "hybrid"

    def _hydrate(self, doc_ids: list[str], snippets: dict[str, str]) -> list[dict]:
        """Build result dicts for documents found by the vector leg only."""
        if not doc_ids:
            return []
        placeholders = ",".join("?" * len(doc_ids))
        rows = self._conn.execute(
            f"SELECT id, repo, kind, source_kind, title, path FROM documents "
            f"WHERE id IN ({placeholders})",
            doc_ids,
        ).fetchall()
        links = self._links_for([r["id"] for r in rows])
        by_id = {
            r["id"]: {
                "id": r["id"],
                "repo": r["repo"],
                "kind": r["kind"],
                "source_kind": r["source_kind"],
                "title": r["title"],
                "path": r["path"],
                "snippet": snippets.get(r["id"], ""),
                "score": None,
                "links": links[r["id"]],
            }
            for r in rows
        }
        return [by_id[i] for i in doc_ids if i in by_id]

    def _fts(
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

    def resolve_attachment_path(self, doc_id: str) -> Path | None:
        """The absolute on-disk path an `attachment:{work_item_id}:{kind}` id
        names — worktree-first, same lookup `_synthesize_attachment_doc` uses
        for content, so `open_document` (Kraft-2jy6) points an editor at the
        file that actually exists pre-merge instead of `doc['repo'] /
        doc['path']`, which is only ever right after the branch lands."""
        _, work_item_id, kind = doc_id.split(":", 2)
        row = self._state.read(
            lambda c: c.execute(
                "SELECT repo, attachments FROM work_items WHERE id = ?", (work_item_id,)
            ).fetchone()
        )
        if row is None or not row["attachments"]:
            return None
        attachment = next((a for a in json.loads(row["attachments"]) if a["kind"] == kind), None)
        if attachment is None:
            return None
        return self._summary_path(attachment["path"], work_item_id, row["repo"])

    def _get_synthetic_attachment_document(self, doc_id: str) -> dict | None:
        """Content fetch for the synthetic `attachment:{work_item_id}:{kind}` ids
        `_synthesize_attachment_doc` hands out — there's no `documents` row to
        join against, so read the file straight from the worktree again."""
        _, work_item_id, kind = doc_id.split(":", 2)
        row = self._state.read(
            lambda c: c.execute(
                "SELECT repo, attachments FROM work_items WHERE id = ?", (work_item_id,)
            ).fetchone()
        )
        if row is None or not row["attachments"]:
            return None
        attachment = next((a for a in json.loads(row["attachments"]) if a["kind"] == kind), None)
        if attachment is None:
            return None
        doc = self._synthesize_attachment_doc(work_item_id, row["repo"], attachment)
        if doc is None:
            return None
        path = self._summary_path(attachment["path"], work_item_id, row["repo"])
        try:
            text = path.read_text() if path is not None else None
        except OSError:
            text = None
        if text is None:
            return None
        _, content = ingest.split_front_matter(text)
        return {
            "id": doc["document_id"],
            "repo": doc["repo"],
            "source_kind": doc["source_kind"],
            "kind": doc["kind"],
            "title": doc["title"],
            "path": doc["path"],
            "content": content,
            "metadata": {},
            "source_created_at": None,
            "source_updated_at": None,
            "indexed_at": doc["indexed_at"],
            "links": [],
        }

    def get_document(self, doc_id: str) -> dict | None:
        if doc_id.startswith("attachment:"):
            return self._get_synthetic_attachment_document(doc_id)
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
            # 'git_scan' (a real path under `repo`) or 'event_ingest' (a
            # session summary or gate artifact -- `path` is a synthetic
            # identifier, not a file the connected repo checkout has).
            "origin": r["origin"],
            "links": self._links_for([doc_id])[doc_id],
        }

    def health(self) -> dict:
        n = self._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunks = self._conn.execute("SELECT COUNT(*) FROM document_chunks").fetchone()[0]
        available = self._embedder.available()
        return {
            "last_scan_at": self._last_scan_at,
            "repos_scanned": self._repos_scanned,
            "documents": n,
            "embeddings": {
                "available": available,
                "model": self._embedder.model_name,
                "chunks": chunks,
                # Unavailable: why not. Available: the last load/encode
                # failure, None once one succeeds (Kraft-pm2rj).
                "reason": self._embedder.reason,
            },
            "errors": list(self._errors),
        }
