# Effort 4B — Session Summaries, Document Links, Work-Item Documents: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest agent-written session summaries into the search index, populate `document_links`, resolve those links into every read path, and expose `GET /work-items/{id}/documents`.

**Architecture:** Summaries are written untracked into a per-work-item git worktree, so the existing `git ls-files` scan of the source repo cannot see them. Ingestion is therefore event-driven: when a worker session exits carrying `session_summary_ref`, the Indexer reads that one file and upserts it. The git scan additionally classifies `.engineering/sessions/` as `source_kind='session_summary'`, so a summary that later merges into the source repo updates the same `(repo, path)` row instead of duplicating it. The summary bucket is upsert-only — it never deletes.

**Tech Stack:** Python 3.14, asyncio, FastAPI, SQLite (FTS5), pytest, uv, ruff.

**Spec:** `docs/superpowers/specs/2026-09-04-effort-4b-session-summaries-design.md`

## Global Constraints

- Backend only. No frontend changes — UI consumption is a separate 4B-UI bead.
- **No index schema change.** `document_links` and its indexes already exist in `src/kraft/index/db.py`. Do not bump `INDEX_SCHEMA_VERSION`.
- `source_kind` stays a two-value closed enum: `'artifact' | 'session_summary'`.
- Session-summary reconcile is upsert-only. Artifact reconcile keeps its existing insert/update/rename/delete diff unchanged.
- For `node_id` / `hook_point` / `worker_session_id`, the `worker_sessions` row wins over front-matter. For `work_item_ids`, front-matter is unioned with the session's own work item.
- The Indexer never blocks the executor: any failure ingesting a summary logs and returns, it does not raise into the drain.
- A `session_summary_ref` is untrusted worker input. Reject absolute paths and any path escaping the worktree before reading.
- Run `uv run ruff check .` and `uv run ruff format .` before each commit. **Do not write `except (A, B):`** — ruff 0.16.5 `format` corrupts that into invalid syntax (Kraft-674); use separate `except` clauses.
- Fast test command: `uv run pytest -m "not e2e and not slow" -q`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/kraft/index/ingest.py` | Pure extraction + reconcile. Gains `source_kind` derivation, front-matter link parsing, a shared upsert, and per-bucket reconcile. | Modify |
| `src/kraft/index/service.py` | Indexer runtime. Gains the event-driven summary ingest, two new drain triggers, link resolution in reads, and the work-item document query. | Modify |
| `src/kraft/api.py` | HTTP surface. Gains one endpoint. | Modify |
| `tests/test_index_ingest.py` | Unit tests for the pure functions and reconcile. | Modify |
| `tests/test_index_service.py` | Indexer-level tests: event ingest, precedence, links in search. | Modify |
| `tests/test_index_triggers.py` | Drain-trigger tests. | Modify |
| `tests/test_search_api.py` | Endpoint tests. | Modify |
| `tests/test_e2e.py` | End-to-end assertion that a completed item has a linked summary. | Modify |

---

### Task 1: Classify summaries and parse their linkage front-matter

**Files:**
- Modify: `src/kraft/index/ingest.py`
- Test: `tests/test_index_ingest.py`

**Interfaces:**
- Consumes: `split_front_matter`, `derive_kind`, `derive_title`, `ScannedDoc` (all existing in `ingest.py`).
- Produces:
  - `SESSIONS_DIR: str = ".engineering/sessions"`
  - `source_kind_for(path: str) -> str` — `'session_summary'` or `'artifact'`
  - `LinkRow` — frozen dataclass with fields `work_item_id: str | None`, `node_id: str | None`, `hook_point: str | None`, `worker_session_id: str | None`, all defaulting to `None`
  - `links_from_front_matter(fm: dict) -> list[LinkRow]`
  - `ScannedDoc` gains `source_kind: str = "artifact"` and `links: tuple[LinkRow, ...] = ()`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_ingest.py`:

```python
# ---- source_kind_for / links_from_front_matter ----


def test_source_kind_for_sessions_folder():
    assert ingest.source_kind_for(".engineering/sessions/s1.md") == "session_summary"
    assert ingest.source_kind_for(".engineering/specs/a.md") == "artifact"
    assert ingest.source_kind_for("README.md") == "artifact"


def test_links_from_front_matter_full():
    links = ingest.links_from_front_matter(
        {
            "work_item_ids": ["w1", "w2"],
            "node_id": "verify",
            "hook_point": "on.test.run",
            "worker_session_id": "s1",
        }
    )
    assert [x.work_item_id for x in links if x.work_item_id] == ["w1", "w2"]
    session_rows = [x for x in links if x.worker_session_id]
    assert len(session_rows) == 1
    assert session_rows[0].node_id == "verify"
    assert session_rows[0].hook_point == "on.test.run"
    assert session_rows[0].work_item_id is None


def test_links_from_front_matter_scalar_work_item_id():
    links = ingest.links_from_front_matter({"work_item_ids": "w1"})
    assert [x.work_item_id for x in links] == ["w1"]


def test_links_from_front_matter_ignores_malformed():
    assert ingest.links_from_front_matter({}) == []
    assert ingest.links_from_front_matter({"work_item_ids": [None, 3, "w1"]}) == [
        ingest.LinkRow(work_item_id="w1")
    ]
    assert ingest.links_from_front_matter({"node_id": 7}) == []


def test_scan_repo_classifies_sessions(tmp_path):
    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/a.md": "# A\nspec body\n",
            ".engineering/sessions/s1.md": (
                "---\nwork_item_ids: [w1]\nnode_id: verify\n"
                "hook_point: on.test.run\nworker_session_id: s1\n---\nsummary body\n"
            ),
        },
    )
    by_path = {d.path: d for d in ingest.scan_repo(repo)}
    assert by_path[".engineering/specs/a.md"].source_kind == "artifact"
    assert by_path[".engineering/specs/a.md"].links == ()
    summary = by_path[".engineering/sessions/s1.md"]
    assert summary.source_kind == "session_summary"
    assert summary.kind == "sessions"
    assert [x.work_item_id for x in summary.links if x.work_item_id] == ["w1"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_ingest.py -k "source_kind or links_from or classifies" -q`
Expected: FAIL with `AttributeError: module 'kraft.index.ingest' has no attribute 'source_kind_for'`

- [ ] **Step 3: Write the minimal implementation**

In `src/kraft/index/ingest.py`, add below `_ATX_HEADING`:

```python
SESSIONS_DIR = ".engineering/sessions"
```

Add after the `ScannedDoc` dataclass:

```python
@dataclass(frozen=True)
class LinkRow:
    work_item_id: str | None = None
    node_id: str | None = None
    hook_point: str | None = None
    worker_session_id: str | None = None
```

Add `source_kind: str = "artifact"` and `links: tuple[LinkRow, ...] = ()` as the last two fields of `ScannedDoc` (defaults keep every existing constructor call valid).

Add after `derive_title`:

```python
def source_kind_for(path: str) -> str:
    """04 §6: everything under .engineering/sessions/ is a session summary."""
    return "session_summary" if path.startswith(f"{SESSIONS_DIR}/") else "artifact"


def _str_or_none(value) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def links_from_front_matter(fm: dict) -> list[LinkRow]:
    """Linkage rows described by a summary's front-matter (04 §6). One row per
    work item, plus one carrying the node/hook/session triple."""
    raw = fm.get("work_item_ids")
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        raw = []
    links = [LinkRow(work_item_id=w) for w in (_str_or_none(x) for x in raw) if w]
    node = _str_or_none(fm.get("node_id"))
    hook = _str_or_none(fm.get("hook_point"))
    session = _str_or_none(fm.get("worker_session_id"))
    if node or hook or session:
        links.append(LinkRow(node_id=node, hook_point=hook, worker_session_id=session))
    return links
```

In `scan_repo`, replace the `docs.append(ScannedDoc(...))` call with:

```python
        source_kind = source_kind_for(rel)
        docs.append(
            ScannedDoc(
                path=rel,
                kind=derive_kind(rel, fm),
                title=derive_title(rel, fm, body),
                content=body,
                content_hash=hashlib.sha256(body.encode()).hexdigest(),
                metadata={k: v for k, v in fm.items() if k not in ("title", "kind")},
                source_created_at=created,
                source_updated_at=updated,
                source_kind=source_kind,
                links=(
                    tuple(links_from_front_matter(fm))
                    if source_kind == "session_summary"
                    else ()
                ),
            )
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_ingest.py -q`
Expected: PASS, all tests in the file

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/kraft/index/ingest.py tests/test_index_ingest.py
git commit -m "feat(Kraft-bj9.2): classify session summaries and parse linkage front-matter"
```

---

### Task 2: Write links, and reconcile the two buckets differently

**Files:**
- Modify: `src/kraft/index/ingest.py`
- Test: `tests/test_index_ingest.py`

**Interfaces:**
- Consumes: `LinkRow`, `source_kind_for`, `ScannedDoc` (Task 1); `_meta_json`, `ReconcileStats`, `_now` (existing).
- Produces:
  - `replace_links(conn, document_id: str, links) -> None` — deletes this document's rows, inserts the given ones
  - `upsert_document(conn, repo: str, doc: ScannedDoc, now: str) -> str` — insert-or-update keyed on `(repo, path)`, returns the document id, replaces its links
  - `reconcile(conn, repo, scanned, *, now=None) -> ReconcileStats` — the `source_kind` keyword argument is **removed**; the bucket now comes from each `ScannedDoc`

**Note on the existing signature:** `reconcile` currently takes `source_kind: str = "artifact"`. No caller passes it (`service.rescan_repo` calls `ingest.reconcile(self._conn, repo, scanned)`), so removing it is safe. Grep to confirm before editing: `grep -rn "reconcile(" src tests`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_index_ingest.py`:

```python
def _docs(conn):
    return {
        r["path"]: r
        for r in conn.execute("SELECT * FROM documents ORDER BY path").fetchall()
    }


def test_reconcile_writes_links_for_summaries_only(tmp_path):
    conn = index_db.open_index(tmp_path / "index.db")
    scanned = [
        ingest.ScannedDoc(
            path=".engineering/specs/a.md",
            kind="specs",
            title="A",
            content="spec",
            content_hash="h1",
            metadata={},
            source_created_at=None,
            source_updated_at=None,
        ),
        ingest.ScannedDoc(
            path=".engineering/sessions/s1.md",
            kind="sessions",
            title="S1",
            content="summary",
            content_hash="h2",
            metadata={},
            source_created_at=None,
            source_updated_at=None,
            source_kind="session_summary",
            links=(
                ingest.LinkRow(work_item_id="w1"),
                ingest.LinkRow(node_id="verify", hook_point="on.test.run", worker_session_id="s1"),
            ),
        ),
    ]
    stats = ingest.reconcile(conn, "/r", scanned)
    assert stats.inserted == 2
    docs = _docs(conn)
    assert docs[".engineering/sessions/s1.md"]["source_kind"] == "session_summary"
    assert docs[".engineering/specs/a.md"]["source_kind"] == "artifact"
    rows = conn.execute(
        "SELECT work_item_id, node_id FROM document_links WHERE document_id=? "
        "ORDER BY COALESCE(work_item_id, '')",
        (docs[".engineering/sessions/s1.md"]["id"],),
    ).fetchall()
    assert [(r["work_item_id"], r["node_id"]) for r in rows] == [(None, "verify"), ("w1", None)]
    assert conn.execute(
        "SELECT COUNT(*) FROM document_links WHERE document_id=?",
        (docs[".engineering/specs/a.md"]["id"],),
    ).fetchone()[0] == 0


def test_reconcile_never_deletes_summaries_but_still_deletes_artifacts(tmp_path):
    conn = index_db.open_index(tmp_path / "index.db")
    first = [
        ingest.ScannedDoc(
            path=".engineering/specs/a.md",
            kind="specs",
            title="A",
            content="spec",
            content_hash="h1",
            metadata={},
            source_created_at=None,
            source_updated_at=None,
        ),
        ingest.ScannedDoc(
            path=".engineering/sessions/s1.md",
            kind="sessions",
            title="S1",
            content="summary",
            content_hash="h2",
            metadata={},
            source_created_at=None,
            source_updated_at=None,
            source_kind="session_summary",
        ),
    ]
    ingest.reconcile(conn, "/r", first)

    # a later scan sees neither file
    stats = ingest.reconcile(conn, "/r", [])
    assert stats.deleted == 1  # the artifact only
    assert set(_docs(conn)) == {".engineering/sessions/s1.md"}


def test_reconcile_summary_update_replaces_links(tmp_path):
    conn = index_db.open_index(tmp_path / "index.db")

    def summary(content_hash, links):
        return ingest.ScannedDoc(
            path=".engineering/sessions/s1.md",
            kind="sessions",
            title="S1",
            content="body",
            content_hash=content_hash,
            metadata={},
            source_created_at=None,
            source_updated_at=None,
            source_kind="session_summary",
            links=links,
        )

    ingest.reconcile(conn, "/r", [summary("h1", (ingest.LinkRow(work_item_id="w1"),))])
    ingest.reconcile(conn, "/r", [summary("h2", (ingest.LinkRow(work_item_id="w2"),))])
    doc_id = _docs(conn)[".engineering/sessions/s1.md"]["id"]
    rows = conn.execute(
        "SELECT work_item_id FROM document_links WHERE document_id=?", (doc_id,)
    ).fetchall()
    assert [r["work_item_id"] for r in rows] == ["w2"]
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_ingest.py -k reconcile -q`
Expected: FAIL — `test_reconcile_writes_links_for_summaries_only` fails asserting `[] == [(None, "verify"), ("w1", None)]` (links are never written today)

- [ ] **Step 3: Write the minimal implementation**

In `src/kraft/index/ingest.py`, add above `reconcile`:

```python
def replace_links(conn, document_id: str, links) -> None:
    """Idempotent: a document's link set is rewritten whole, never appended to."""
    conn.execute("DELETE FROM document_links WHERE document_id=?", (document_id,))
    for link in links:
        conn.execute(
            "INSERT INTO document_links (id, document_id, work_item_id, node_id, "
            "hook_point, worker_session_id) VALUES (?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                document_id,
                link.work_item_id,
                link.node_id,
                link.hook_point,
                link.worker_session_id,
            ),
        )


def upsert_document(conn, repo: str, doc: ScannedDoc, now: str) -> str:
    """Insert or update one document keyed on (repo, path), replacing its links.
    Shared by the git scan and the event-driven summary path."""
    row = conn.execute(
        "SELECT id FROM documents WHERE repo=? AND path=?", (repo, doc.path)
    ).fetchone()
    if row is None:
        doc_id = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO documents (id, repo, source_kind, kind, title, path, content, "
            "content_hash, metadata_json, source_created_at, source_updated_at, indexed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                doc_id,
                repo,
                doc.source_kind,
                doc.kind,
                doc.title,
                doc.path,
                doc.content,
                doc.content_hash,
                _meta_json(doc.metadata),
                doc.source_created_at,
                doc.source_updated_at,
                now,
            ),
        )
    else:
        doc_id = row["id"]
        conn.execute(
            "UPDATE documents SET source_kind=?, kind=?, title=?, content=?, content_hash=?, "
            "metadata_json=?, source_created_at=?, source_updated_at=?, indexed_at=? WHERE id=?",
            (
                doc.source_kind,
                doc.kind,
                doc.title,
                doc.content,
                doc.content_hash,
                _meta_json(doc.metadata),
                doc.source_created_at,
                doc.source_updated_at,
                now,
                doc_id,
            ),
        )
    replace_links(conn, doc_id, doc.links)
    return doc_id
```

Then change `reconcile` to split the buckets. Replace its signature and the two lines that follow it:

```python
def reconcile(
    conn,
    repo: str,
    scanned: list[ScannedDoc],
    *,
    now: str | None = None,
) -> ReconcileStats:
    now = now or _now()
    summaries = [d for d in scanned if d.source_kind == "session_summary"]
    scanned = [d for d in scanned if d.source_kind == "artifact"]
    source_kind = "artifact"
```

Leave the entire existing artifact body untouched below that. Inside the existing
`conn.execute("BEGIN")` block, after the final update loop and before the commit,
add the upsert-only summary pass:

```python
        # 04 §5 as amended by the 4B design: the summary bucket is upsert-only.
        # A live summary lives in a worktree, so every scan of the source repo
        # would otherwise "miss" it and delete it.
        for d in summaries:
            existed = conn.execute(
                "SELECT 1 FROM documents WHERE repo=? AND path=?", (repo, d.path)
            ).fetchone()
            upsert_document(conn, repo, d, now)
            if existed is None:
                ins += 1
            else:
                upd += 1
```

Finally, in the artifact insert loop, replace the raw `INSERT` with a call that also
writes links so the two paths stay in step — change the body of `for p in new_paths:`
to:

```python
        for p in new_paths:
            upsert_document(conn, repo, by_path[p], now)
            ins += 1
```

and in the rename and content-change loops, add `replace_links(conn, <that doc id>, d.links)` immediately after each `UPDATE documents ...` statement.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_ingest.py tests/test_index_service.py -q`
Expected: PASS. If `test_startup_scan_then_search` fails on counts, the artifact bucket's stats accounting was changed — the artifact diff must keep its existing insert/update/rename/delete semantics.

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/kraft/index/ingest.py tests/test_index_ingest.py
git commit -m "feat(Kraft-bj9.2): write document_links; upsert-only reconcile for summaries"
```

---

### Task 3: Ingest a summary from its ref when a session exits

**Files:**
- Modify: `src/kraft/index/service.py`
- Test: `tests/test_index_service.py`

**Interfaces:**
- Consumes: `ingest.upsert_document`, `ingest.LinkRow`, `ingest.split_front_matter`, `ingest.derive_title`, `ingest.derive_kind`, `ingest.links_from_front_matter`, `ingest.source_kind_for` (Tasks 1–2); `RunDirs` from `kraft.paths`.
- Produces:
  - `Indexer.__init__` gains a keyword argument `run_dirs=None`
  - `Indexer.ingest_session_summary(session_id: str) -> bool`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_service.py`:

```python
def _seed_session(db, sid, wid, ref, node="implementation", hook="on.implementation.start"):
    from kraft import store

    return db.write(
        lambda c: (
            store.create_session(
                c,
                id=sid,
                work_item_id=wid,
                node_id=node,
                hook_point=hook,
                log_path="/dev/null",
                result_path="/dev/null",
            ),
            c.execute(
                "UPDATE worker_sessions SET session_summary_ref=? WHERE id=?", (ref, sid)
            ),
        )
    )


def test_ingest_session_summary_from_worktree(tmp_path):
    from kraft.paths import RunDirs

    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
            await _seed_work_item(state, str(repo))
            rd = RunDirs(tmp_path / "run").ensure()
            wt = rd.worktrees / "w1" / ".engineering" / "sessions"
            wt.mkdir(parents=True)
            (wt / "s1.md").write_text(
                "---\ntitle: Implementation session\nwork_item_ids: [w1, w2]\n"
                "node_id: bogus\nhook_point: on.bogus\nworker_session_id: s1\n---\n"
                "rewrote the adapter\n"
            )
            await _seed_session(state, "s1", "w1", ".engineering/sessions/s1.md")

            ix = Indexer(conn, state, repos_env="", run_dirs=rd)
            assert await ix.ingest_session_summary("s1") is True

            hits = ix.search("adapter")
            assert [h["path"] for h in hits] == [".engineering/sessions/s1.md"]
            assert hits[0]["source_kind"] == "session_summary"
            assert hits[0]["repo"] == str(repo)
            assert hits[0]["title"] == "Implementation session"

            doc_id = hits[0]["id"]
            rows = conn.execute(
                "SELECT work_item_id, node_id, hook_point, worker_session_id "
                "FROM document_links WHERE document_id=? ORDER BY COALESCE(work_item_id, '')",
                (doc_id,),
            ).fetchall()
            # DB wins for the session triple; front-matter's bogus values are dropped
            assert rows[0]["node_id"] == "implementation"
            assert rows[0]["hook_point"] == "on.implementation.start"
            assert rows[0]["worker_session_id"] == "s1"
            # front-matter work items unioned with the session's own
            assert sorted(r["work_item_id"] for r in rows if r["work_item_id"]) == ["w1", "w2"]

            # idempotent
            assert await ix.ingest_session_summary("s1") is True
            assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
            assert conn.execute(
                "SELECT COUNT(*) FROM document_links WHERE document_id=?", (doc_id,)
            ).fetchone()[0] == 3
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())


def test_ingest_session_summary_missing_file_is_false(tmp_path):
    from kraft.paths import RunDirs

    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
            await _seed_work_item(state, str(repo))
            await _seed_session(state, "s1", "w1", ".engineering/sessions/nope.md")
            ix = Indexer(conn, state, repos_env="", run_dirs=RunDirs(tmp_path / "run").ensure())
            assert await ix.ingest_session_summary("s1") is False
            assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())


def test_ingest_session_summary_rejects_path_escape(tmp_path):
    from kraft.paths import RunDirs

    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
            await _seed_work_item(state, str(repo))
            rd = RunDirs(tmp_path / "run").ensure()
            (tmp_path / "secret.md").write_text("# secret\ndo not index\n")
            await _seed_session(state, "s1", "w1", "../../secret.md")
            await _seed_session(state, "s2", "w1", "/etc/hosts")
            ix = Indexer(conn, state, repos_env="", run_dirs=rd)
            assert await ix.ingest_session_summary("s1") is False
            assert await ix.ingest_session_summary("s2") is False
            assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_index_service.py -k session_summary -q`
Expected: FAIL with `TypeError: Indexer.__init__() got an unexpected keyword argument 'run_dirs'`

- [ ] **Step 3: Write the minimal implementation**

In `src/kraft/index/service.py`, add `import hashlib` and `from pathlib import Path` (already imported) at the top, then extend the constructor:

```python
    def __init__(
        self, index_conn, state_db, *, repos_env: str | None = None, run_dirs=None
    ) -> None:
        self._conn = index_conn
        self._state = state_db
        self._run_dirs = run_dirs
```

(keep the remaining existing constructor body unchanged)

Add these methods in the ingestion section, after `startup_scan`:

```python
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
```

In `src/kraft/api.py:105`, pass the run dirs through:

```python
    indexer = Indexer(
        index_conn, database, repos_env=os.environ.get("KRAFT_INDEX_REPOS"), run_dirs=run_dirs
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_service.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/kraft/index/service.py src/kraft/api.py tests/test_index_service.py
git commit -m "feat(Kraft-bj9.2): ingest session summaries from session_summary_ref"
```

---

### Task 4: Drive ingestion and the env-prepare rescan from the event bus

**Files:**
- Modify: `src/kraft/index/service.py` (the `_run` drain loop)
- Test: `tests/test_index_triggers.py`

**Interfaces:**
- Consumes: `Indexer.ingest_session_summary` (Task 3), `Indexer.rescan_repo` (existing).
- Produces: no new public names; the drain reacts to `worker_session_exited` and to `worker_session_started` where `payload["hook_point"] == "on.env.prepare"`.

- [ ] **Step 1: Write the failing test**

Read `tests/test_index_triggers.py` first and follow its existing pattern for starting the drain and awaiting a settle. Append:

```python
def test_session_exit_with_ref_ingests_summary(tmp_path):
    from kraft import store
    from kraft.paths import RunDirs

    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
            await _seed_work_item(state, str(repo))
            rd = RunDirs(tmp_path / "run").ensure()
            sessions = rd.worktrees / "w1" / ".engineering" / "sessions"
            sessions.mkdir(parents=True)
            (sessions / "s1.md").write_text("# Session\nrewired the drain\n")

            ix = Indexer(conn, state, repos_env="", run_dirs=rd)
            state.set_on_commit(ix.notify)
            await ix.start()
            try:
                await state.write(
                    lambda c: store.create_session(
                        c,
                        id="s1",
                        work_item_id="w1",
                        node_id="implementation",
                        hook_point="on.implementation.start",
                        log_path="/dev/null",
                        result_path="/dev/null",
                    )
                )
                await state.write(
                    lambda c: store.session_exited(
                        c, "s1", "done", ".engineering/sessions/s1.md"
                    )
                )
                for _ in range(100):
                    await asyncio.sleep(0.02)
                    if ix.search("drain"):
                        break
                assert [h["path"] for h in ix.search("drain")] == [
                    ".engineering/sessions/s1.md"
                ]
            finally:
                await ix.stop()
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_index_triggers.py -k session_exit -q`
Expected: FAIL on the assertion — the drain ignores `worker_session_exited`, so `ix.search("drain")` stays empty

- [ ] **Step 3: Write the minimal implementation**

In `src/kraft/index/service.py`, inside `_run`, replace the body of the `for ev in new:` loop with:

```python
                repos: list[str] = []
                summaries: list[str] = []
                for ev in new:
                    self._cursor = ev["seq"]
                    payload = ev["payload"]
                    if isinstance(payload, str):
                        payload = json.loads(payload)
                    if ev["type"] == "work_item_completed":
                        row = self._state.read(
                            lambda c, wid=ev["work_item_id"]: c.execute(
                                "SELECT repo FROM work_items WHERE id=?", (wid,)
                            ).fetchone()
                        )
                        if row is not None and row["repo"] not in repos:
                            repos.append(row["repo"])
                    elif ev["type"] == "worker_session_exited":
                        summaries.append(payload["session_id"])
                    elif (
                        ev["type"] == "worker_session_started"
                        and payload.get("hook_point") == "on.env.prepare"
                    ):
                        # 04 §2 piggyback: a work item starting on a repo is a
                        # good moment to refresh that repo's artifacts.
                        row = self._state.read(
                            lambda c, wid=ev["work_item_id"]: c.execute(
                                "SELECT repo FROM work_items WHERE id=?", (wid,)
                            ).fetchone()
                        )
                        if row is not None and row["repo"] not in repos:
                            repos.append(row["repo"])
                for sid in summaries:
                    try:
                        await self.ingest_session_summary(sid)
                    except Exception:
                        logger.exception("session summary ingest failed for %s", sid)
                for repo in repos:
                    try:
                        await self.rescan_repo(repo)
                    except Exception:  # noqa: BLE001
                        logger.exception("triggered rescan failed for %s", repo)
```

Check how `events.read_after` returns `payload` before writing this — if it already decodes JSON, drop the two `isinstance` lines. Run `grep -n "def read_after" -A 15 src/kraft/events.py`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_triggers.py -q`
Expected: PASS, including the pre-existing trigger tests

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/kraft/index/service.py tests/test_index_triggers.py
git commit -m "feat(Kraft-bj9.2): drain worker_session_exited and on.env.prepare triggers"
```

---

### Task 5: Resolve links in every read path

**Files:**
- Modify: `src/kraft/index/service.py`
- Test: `tests/test_index_service.py`

**Interfaces:**
- Consumes: `document_links` rows written by Tasks 2–3.
- Produces:
  - `Indexer._links_for(doc_ids: list[str]) -> dict[str, list[dict]]` — one batched query
  - `Indexer.documents_for_work_item(work_item_id: str) -> list[dict]`
  - `search()` and `get_document()` return real `links` lists instead of `[]`

Each link dict has keys `work_item_id`, `node_id`, `hook_point`, `worker_session_id`.
Each `documents_for_work_item` entry has keys `document_id`, `repo`, `title`, `kind`,
`source_kind`, `path`, `node_id`, `hook_point`, `worker_session_id`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_index_service.py`:

```python
def test_links_resolved_in_search_and_document_and_by_work_item(tmp_path):
    from kraft.paths import RunDirs

    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"})
            await _seed_work_item(state, str(repo))
            rd = RunDirs(tmp_path / "run").ensure()
            sessions = rd.worktrees / "w1" / ".engineering" / "sessions"
            sessions.mkdir(parents=True)
            (sessions / "s1.md").write_text("# Session\nlinked prose\n")
            await _seed_session(state, "s1", "w1", ".engineering/sessions/s1.md")

            ix = Indexer(conn, state, repos_env="", run_dirs=rd)
            await ix.startup_scan()
            await ix.ingest_session_summary("s1")

            hit = ix.search("linked")[0]
            assert {ln["work_item_id"] for ln in hit["links"] if ln["work_item_id"]} == {"w1"}
            assert any(ln["worker_session_id"] == "s1" for ln in hit["links"])

            doc = ix.get_document(hit["id"])
            assert doc["links"] == hit["links"]

            # an artifact has no links, and still reports an empty list
            spec = ix.search("x")[0]
            assert spec["links"] == []

            docs = ix.documents_for_work_item("w1")
            assert [d["path"] for d in docs] == [".engineering/sessions/s1.md"]
            assert docs[0]["title"] == "Session"
            assert docs[0]["source_kind"] == "session_summary"
            assert ix.documents_for_work_item("nope") == []
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_index_service.py -k links_resolved -q`
Expected: FAIL asserting `set() == {"w1"}` — `search` hardcodes `"links": []`

- [ ] **Step 3: Write the minimal implementation**

In `src/kraft/index/service.py`, add to the queries section:

```python
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
        rows = self._conn.execute(
            "SELECT d.id AS document_id, d.repo, d.title, d.kind, d.source_kind, d.path, "
            "       l.node_id, l.hook_point, l.worker_session_id "
            "FROM document_links l JOIN documents d ON d.id = l.document_id "
            "WHERE l.work_item_id = ? ORDER BY d.path",
            (work_item_id,),
        ).fetchall()
        return [{k: r[k] for k in r.keys()} for r in rows]
```

In `search`, replace the return block's `"links": []` by resolving first:

```python
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
```

In `get_document`, replace `"links": []` with `"links": self._links_for([doc_id])[doc_id]`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_index_service.py tests/test_search_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/kraft/index/service.py tests/test_index_service.py
git commit -m "feat(Kraft-bj9.2): resolve document_links in search and document reads"
```

---

### Task 6: `GET /work-items/{id}/documents`

**Files:**
- Modify: `src/kraft/api.py`
- Test: `tests/test_search_api.py`

**Interfaces:**
- Consumes: `Indexer.documents_for_work_item` (Task 5), the existing `_work_item_row(st, wid)` helper in `api.py`.
- Produces: `GET /work-items/{wid}/documents` → `{"work_item_id": str, "documents": [...]}`; 404 when the work item is unknown.

- [ ] **Step 1: Write the failing test**

Read `tests/test_search_api.py`'s `_client` fixture first and follow it. Append:

```python
def test_work_item_documents_endpoint(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/work-items/nope/documents").status_code == 404
        wid = client.post(
            "/work-items", json={"title": "t", "repo": str(tmp_path)}
        ).json()["id"]
        r = client.get(f"/work-items/{wid}/documents")
        assert r.status_code == 200
        assert r.json() == {"work_item_id": wid, "documents": []}
```

If `_client` does not accept work-item creation (no templates configured), assert only the 404 case plus a 200 with `documents: []` for a work item seeded directly through `client.app.state.db`. Match whatever the file already does.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_search_api.py -k work_item_documents -q`
Expected: FAIL — the route does not exist, so the 200 case returns 404

- [ ] **Step 3: Write the minimal implementation**

In `src/kraft/api.py`, add immediately after the `get_events` handler:

```python
@app.get("/work-items/{wid}/documents")
async def get_work_item_documents(wid: str, request: Request):
    st = request.app.state
    _work_item_row(st, wid)  # 404s on an unknown work item
    return {
        "work_item_id": wid,
        "documents": st.indexer.documents_for_work_item(wid),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_search_api.py tests/test_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check . && uv run ruff format .
git add src/kraft/api.py tests/test_search_api.py
git commit -m "feat(Kraft-bj9.2): add GET /work-items/{id}/documents"
```

---

### Task 7: Prove it end to end, and close out

**Files:**
- Modify: `tests/test_e2e.py`
- Modify: `docs/consolidated/04_indexer_search.md` (build-order step 4)

**Interfaces:**
- Consumes: everything above. `fixtures/fake-claude.sh` already writes a summary and reports `session_summary_ref` (Kraft-bj9.7), so the e2e run produces real input with no fixture change.

- [ ] **Step 1: Write the failing assertion**

In `tests/test_e2e.py::test_e2e_happy_path`, after the existing `assert item["status"] == "completed"` line, add:

```python
        docs = srv.client.get(f"/work-items/{wid}/documents").json()["documents"]
        summaries = [d for d in docs if d["source_kind"] == "session_summary"]
        assert summaries, f"no session summary linked to {wid}; got {docs}"
        assert summaries[0]["path"].startswith(".engineering/sessions/")
        assert summaries[0]["worker_session_id"] or any(
            d["worker_session_id"] for d in docs
        )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest -m e2e -q`
Expected: FAIL on the new assertion if any task above is incomplete. If the whole file **skips**, `claude` is absent from this machine — that is acceptable; note it in the handoff and rely on Task 4's drain test for coverage.

- [ ] **Step 3: Make it pass**

No new code should be needed. If it fails, the cause is almost certainly one of:
- the Indexer was constructed without `run_dirs` (Task 3's `api.py` change), so the worktree file is never found;
- `state.set_on_commit(ix.notify)` is not wired in `api.py`, so the drain never wakes (check with `grep -n "set_on_commit" src/kraft/api.py`);
- the summary landed in the worktree but the ref stored is absolute (check `worker_sessions.session_summary_ref` in the run's DB).

Fix the actual cause; do not weaken the assertion.

- [ ] **Step 4: Update the build-order doc**

In `docs/consolidated/04_indexer_search.md` §11, mark step 4 done and record the deviation:

```markdown
4. ✅ **Session-summary ingestion + node/task linkage** (Effort 4B, bead Kraft-bj9.2) —
   event-driven off `worker_sessions.session_summary_ref` rather than by git scan,
   because a live summary sits untracked in the work item's worktree; the scan
   classifies `.engineering/sessions/` as `session_summary` so merged summaries
   update the same row. Summary reconcile is upsert-only. Also lands
   `GET /work-items/{id}/documents`, links resolved inline in `/search` and
   `/documents/{id}`, and the `on.env.prepare` piggyback rescan. See
   `docs/superpowers/specs/2026-09-04-effort-4b-session-summaries-design.md`.
```

- [ ] **Step 5: Run the full suite and commit**

```bash
uv run ruff check . && uv run ruff format .
uv run pytest -m "not e2e" -q
git add tests/test_e2e.py docs/consolidated/04_indexer_search.md
git commit -m "test(Kraft-bj9.2): e2e asserts session summary linked; mark build step 4 done"
```

- [ ] **Step 6: File the follow-up bead and close this one**

```bash
bd create --title="Effort 4B-UI: linked documents panel + link chips in search" --type=feature --priority=3 --parent=Kraft-bj9 --description="Consume 4B's backend in the SPA (doc 05): a linked-documents panel on work-item detail fed by GET /work-items/{id}/documents, and link chips on search results now that /search resolves document_links inline. Mirrors the 4A/4A-UI split."
bd close Kraft-bj9.2 --reason="Effort 4B backend landed"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §2 event-driven discovery | 3, 4 |
| §2 scan classifies `.engineering/sessions/` | 1 |
| §3 upsert-only summary reconcile | 2 |
| §4 DB-over-front-matter precedence, work-item union | 3 |
| §4 replace-all link writes | 2 |
| §5.1 ingest functions | 1, 2 |
| §5.2 Indexer methods, drain triggers, link resolution | 3, 4, 5 |
| §5.3 API endpoint | 6 |
| §7 error handling incl. path traversal | 3 |
| §8 testing | every task; e2e in 7 |

No gaps.

**Type consistency:** `LinkRow` field names (`work_item_id`, `node_id`, `hook_point`, `worker_session_id`) match the `document_links` columns and the dicts returned by `_links_for`. `upsert_document(conn, repo, doc, now) -> str` is used identically in Tasks 2 and 3. `ScannedDoc`'s two new fields have defaults, so every pre-existing constructor call still type-checks.

**One caution for the executor:** Task 2 removes `reconcile`'s `source_kind` keyword argument. Grep for callers before editing — at the time of writing only `service.rescan_repo` calls it, and it does not pass that argument.
