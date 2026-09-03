# Effort 4A — Indexer core + artifact ingestion + FTS `/search`

**Status:** design agreed
**Date:** 2026-09-04
**Epic:** Kraft-bj9 · **Bead:** Kraft-bj9.1
**Design source:** `docs/consolidated/04_indexer_search.md` (Component 3)
**Refines:** `02_orchestrator_core.md` §4/§5 (state model, event bus), `01` §6

---

## 0. Why a slice

Doc 04's build order is 6 steps. Steps 4 and 6 have hard external
dependencies that do not exist yet:

- **Step 4 (session summaries)** needs the Execution Worker to write
  `.engineering/sessions/*.md` and expose `session_summary_ref` in its
  result file (`03` §2/§3). No chain node produces `.engineering/`
  content today — the current `quick-task` / `default` chains only edit
  target source and run tests.
- **Step 6 (vector search)** is "last since it's the piece most likely to
  need iteration" (04 §7/§11) and needs a local embedding model choice
  (04 §10 open question) plus a new dependency.

So Effort 4 ships like Effort 3 did: **4A now** (steps 1, 2, 3, 5), **4B**
(step 4, blocked on the Execution Worker), **4C** (step 6, deferred).

4A is still real, testable, end-to-end work: it stands up the whole index
subsystem — second DB, ingestion engine, the open-tag `kind` mechanism,
the rename/delete diff, live + startup triggers, and a usable FTS search
API. Tests drive it with fixture repos that carry hand-authored
`.engineering/**/*.md` files, exactly as 04 §11 step 2 intends
("Exercises the open-tag `kind` mechanism end to end first").

---

## 1. Scope

### In

| # | From doc 04 | 4A deliverable |
|---|---|---|
| 1 | §3, §11.1 | Index DB: second SQLite file, `documents` + `documents_fts` + `document_links` (created now, populated by 4B), own migration ladder, disposable/rebuildable |
| 2 | §4, §5, §11.2 | Artifact ingestion: per-repo `git ls-files` scan of `.engineering/**/*.md`, YAML front-matter parse, folder-derived open-tag `kind` (front-matter override), content-hash insert/delete/rename/re-extract diff |
| 3 | §2, §11.3 | Triggers: event-bus subscription (live) + one startup full scan + `POST /index/rescan`. No poll loop. |
| 5 | §9, §11.5 | `GET /search` (`mode=fts` only), `GET /documents/{id}`, `POST /index/rescan` |

### Out (own beads)

- **4B (Kraft-bj9.2):** `source_kind='session_summary'` ingestion,
  `document_links` population, `GET /work-items/{id}/documents`,
  `worker_sessions` cross-check. Blocked on the Execution Worker.
- **4C (Kraft-bj9.3):** `sqlite-vec`, embeddings, `document_chunks`,
  `mode=vector|hybrid`. Deferred.
- **Search UI** — component 4 (`05`), already a deferred UI slice in the
  walking-skeleton master plan. 4A is backend only, "same tier as `02`
  §10's `/work-items` surface" (04 §9).
- **`GET /beads/search`** — component 5 (`06` §6.1), not this component.
- **`on.env.prepare` piggyback rescan** (04 §2 bullet 3) — deferred to 4B;
  the startup scan + `work_item_completed` trigger + manual rescan cover
  4A's needs, and `env_setup` today is a plain `git worktree add` builtin
  with no natural hook to ride.

---

## 2. Decisions made in this design

These resolve ambiguity in doc 04 for the 4A slice. Flagged for review.

1. **Slice split 4A / 4B / 4C** as above.
2. **Connected-repo set** = `SELECT DISTINCT repo FROM work_items`, plus an
   optional `KRAFT_INDEX_REPOS` env var (`os.pathsep`-separated absolute
   paths) as the escape hatch for repos the orchestrator has not yet seen
   a work item for. Non-directories are skipped with a logged warning.
   Doc 04 assumes "~10 connected repos" but the orchestrator has no repo
   registry yet; this derives the set from what it actually knows.
   `ponytail:` upgrade path — a real `connected_repos` config/table when
   federation (component 5) lands.
3. **Index DB access model** = a single `sqlite3.Connection` used only on
   the orchestrator event loop thread. No writer queue, no reader
   connection. The index is disposable, low-QPS, and every writer path is
   already on-loop (event handler, startup, endpoint). Blocking git scans
   run in `asyncio.to_thread`; the SQLite writes after them are fast and
   on-loop. `ponytail:` comment names the ceiling — move ingestion writes
   off-thread behind a queue if a scan of many large repos ever stalls the
   loop.
4. **Disposable rebuild** = on open, if `PRAGMA user_version` is newer than
   the code, or the schema is missing/corrupt, delete the file and
   recreate. No migration is written to preserve index data — 04 §1/§5
   say a wrong drop "costs nothing". Forward migrations between 4A→4B→4C
   versions are still additive DDL (new tables), applied in order.
5. **Bus subscription** = an `on_commit` multiplexer in `api.py` lifespan:
   `database.set_on_commit(lambda: (broadcaster.notify(), indexer.notify()))`.
   The `Indexer` keeps its own `_cursor` and reads `events.read_after`
   exactly as `Broadcaster` does — an established pattern in this codebase,
   no new bus machinery, and no coupling to the broadcaster's
   client-overflow semantics.
6. **Live trigger set for 4A** = `work_item_completed` only. That is the
   one current event after which a repo's `.engineering/` tree could have
   changed (an agent that wrote artifacts during the run). 4B widens this
   to artifact-producing `worker_session_exited` events once those hooks
   exist. A missed change is caught by the next startup scan or a manual
   rescan — the index is not authoritative.
7. **`title` derivation** = front-matter `title:` → first Markdown ATX
   heading (`# ...`) in the body → file stem. Deterministic, no config.
8. **Snippet** = FTS5 `snippet()` over `content`, 64-token window,
   `…` ellipsis, `[` / `]` match markers. Fixed, no knob.
9. **`/search` with no `q`** → `422`. FTS needs a query string; "list
   everything" is not this endpoint's job (that is `/work-items/{id}/documents`,
   which is 4B).

---

## 3. Schema (index DB)

Second SQLite file: `RunDirs.index_db` → `<KRAFT_RUN_DIR>/index.db`.
WAL, `foreign_keys=ON`, `synchronous=NORMAL` — same pragmas as the state
DB.

```sql
CREATE TABLE documents (
  id                TEXT PRIMARY KEY,        -- uuid at first sighting, stable across rename
  repo              TEXT NOT NULL,
  source_kind       TEXT NOT NULL CHECK (source_kind IN ('artifact', 'session_summary')),
  kind              TEXT,                    -- OPEN TAG: folder segment after .engineering/,
                                             -- overridden by front-matter `kind:`. nullable.
  title             TEXT NOT NULL,
  path              TEXT NOT NULL,           -- repo-relative posix path
  content           TEXT NOT NULL,           -- markdown body, front-matter stripped
  content_hash      TEXT NOT NULL,           -- sha256 hex of content
  metadata_json     TEXT NOT NULL DEFAULT '{}',
  source_created_at TEXT,                    -- git log --diff-filter=A --follow, first commit
  source_updated_at TEXT,                    -- git log -1, last commit touching path
  indexed_at        TEXT NOT NULL,
  UNIQUE (repo, path)
);

CREATE INDEX idx_documents_repo_kind ON documents(repo, source_kind, kind);

CREATE VIRTUAL TABLE documents_fts USING fts5 (
  title, content,
  content='documents', content_rowid='rowid'
);

-- external-content triggers keep the FTS shadow in sync
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

CREATE TABLE document_links (   -- created now, populated by 4B
  id                TEXT PRIMARY KEY,
  document_id       TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  work_item_id      TEXT,
  node_id           TEXT,
  hook_point        TEXT,
  worker_session_id TEXT
);

CREATE INDEX idx_document_links_work_item ON document_links(work_item_id);
CREATE INDEX idx_document_links_document ON document_links(document_id);
```

`INDEX_SCHEMA_VERSION = 1` for 4A. `rowid` (implicit) is the FTS join key;
`id` stays the stable external identity for `document_links` / future
`document_vectors`.

`ponytail:` the `documents_fts` external-content triggers are the standard
SQLite FTS5 pattern — not hand-rolled sync.

---

## 4. Modules

```
src/kraft/index/
  __init__.py
  db.py        -- connect + migrate + disposable rebuild; INDEX_SCHEMA_SQL
  ingest.py    -- scan_repo() (git + front-matter + hash), reconcile() (§5 diff)
  service.py   -- Indexer: startup_scan, notify/drain loop, rescan_repo, search, get_document
```

`src/kraft/paths.py` — add `RunDirs.index_db`.
`src/kraft/api.py` — 3 endpoints + lifespan wiring.

### 4.1 `index/db.py`

```python
INDEX_SCHEMA_VERSION = 1

def connect(path: Path) -> sqlite3.Connection: ...   # pragmas, row_factory

def open_index(path: Path) -> sqlite3.Connection:
    """Connect + migrate. If user_version > code or schema unusable, delete and rebuild."""
```

Migration ladder mirrors `kraft/db.py`: a `_MIGRATIONS: dict[int, list[str]]`
that is empty for 4A, `BEGIN` / bump `user_version` / `COMMIT` per step.
The "newer than code" and "corrupt" branches `path.unlink(missing_ok=True)`
then recreate from `INDEX_SCHEMA_SQL`.

### 4.2 `index/ingest.py`

```python
@dataclass(frozen=True)
class ScannedDoc:
    path: str            # repo-relative posix
    kind: str | None
    title: str
    content: str
    content_hash: str
    metadata: dict
    source_created_at: str | None
    source_updated_at: str | None

def scan_repo(repo: Path) -> list[ScannedDoc]:
    # git -C repo ls-files -z -- '.engineering/*.md' '.engineering/**/*.md'
    # for each: read text, split_front_matter(), derive kind/title, sha256(body)
    # git log timestamps per path

def split_front_matter(text: str) -> tuple[dict, str]:
    # leading '---\n' ... '\n---\n' -> (yaml.safe_load or {}, body); else ({}, text)

def reconcile(conn, repo: str, scanned: list[ScannedDoc]) -> ReconcileStats:
    # §5 diff for this (repo, source_kind='artifact'):
    #   rows = {path: (id, content_hash)} from documents WHERE repo=? AND source_kind='artifact'
    #   scanned_by_path = {d.path: d}
    #   inserts  = paths in scanned not in rows                       -> INSERT (new uuid)
    #   gone     = paths in rows not in scanned
    #   appeared = inserts; match gone.hash == appeared.hash          -> UPDATE path, keep id (rename)
    #   remaining gone                                                -> DELETE (cascade fts/links)
    #   same path, hash changed                                       -> UPDATE content/title/meta/hash
    #   same path, hash same                                          -> touch nothing
    # all in one transaction
```

`kind` = first path segment after `.engineering/` (`.engineering/specs/x.md`
→ `specs`; `.engineering/x.md` → `None`), unless front-matter has `kind:`.

`scan_repo` runs under `asyncio.to_thread` from the caller; `reconcile`
runs on-loop against the index connection.

### 4.3 `index/service.py`

```python
class Indexer:
    def __init__(self, conn, state_db, *, repos_env: str | None): ...
    @property
    def last_scan(self) -> ScanSummary          # for /health

    async def startup_scan(self) -> ScanSummary  # bounded gather over repos
    def notify(self) -> None                      # set an asyncio.Event
    async def _run(self) -> None                  # drain: read events.read_after(cursor),
                                                  #   on work_item_completed -> queue repo
                                                  #   coalesce, rescan_repo each
    async def start(self) / stop(self)
    async def rescan_repo(self, repo: str) -> ReconcileStats
    def search(self, q, *, source_kind, kind, repo, limit) -> list[dict]
    def get_document(self, doc_id) -> dict | None
```

`_repos()` = `{r["repo"] for r in state_db.read("SELECT DISTINCT repo FROM work_items")}`
∪ `KRAFT_INDEX_REPOS` split, filtered to existing dirs.

Live loop coalesces: a burst of events for the same repo collapses to one
`rescan_repo`. Cursor advances past every event read, not just triggers.

`search` SQL:

```sql
SELECT d.id, d.repo, d.kind, d.source_kind, d.title, d.path,
       snippet(documents_fts, 1, '[', ']', '…', 64) AS snippet,
       bm25(documents_fts) AS score
FROM documents_fts
JOIN documents d ON d.rowid = documents_fts.rowid
WHERE documents_fts MATCH :q
  [AND d.source_kind = :source_kind] [AND d.kind = :kind] [AND d.repo = :repo]
ORDER BY score           -- bm25: more negative = better
LIMIT :limit
```

Each result carries `"links": []` in 4A (populated in 4B). The MATCH query
string is passed to FTS5 as-is (FTS5 query syntax); a malformed query
returns `422` with the SQLite error, not a 500.

---

## 5. API

All localhost tier, no auth — same as `/work-items` (`02` §10, 04 §9).

### `GET /search`

Query params: `q` (required), `source_kind`, `kind`, `repo`, `mode`
(default `fts`; 4A rejects anything but `fts` with `422`), `limit`
(default 20, max 100).

```json
{
  "query": "reconnect backoff",
  "mode": "fts",
  "results": [
    {
      "id": "…", "repo": "/abs/repo", "source_kind": "artifact",
      "kind": "specs", "title": "WS transport design", "path": ".engineering/specs/ws.md",
      "snippet": "…the [reconnect] [backoff] schedule caps at…", "score": -1.83,
      "links": []
    }
  ]
}
```

- `q` missing/empty → `422`.
- Malformed FTS query → `422` (`{"detail": "<sqlite error>"}`).
- `mode` other than `fts` → `422`.

### `GET /documents/{id}`

Full row: `content` (whole markdown body), `metadata` (parsed
`metadata_json`), `kind`, `source_kind`, `repo`, `path`,
`source_created_at`, `source_updated_at`, `indexed_at`, `links: []`.
Unknown id → `404`.

### `POST /index/rescan`

Query param `repo` optional. Given → rescan that one repo (must be in the
known set, else `404`). Omitted → rescan all known repos.
Returns `{"repo": …|null, "stats": {"inserted": n, "updated": n, "renamed": n, "deleted": n}}`.

### `GET /health` (extend)

Add `"index": {"last_scan_at": …, "repos_scanned": n, "documents": n, "errors": [...]}`.

---

## 6. Lifespan wiring (`api.py`)

Order in `lifespan`, after the existing state-DB + reattach block, before
`yield`:

1. `index_conn = index_db.open_index(run_dirs.index_db)`
2. `indexer = Indexer(index_conn, database, repos_env=os.environ.get("KRAFT_INDEX_REPOS"))`
3. `await indexer.startup_scan()` — 04 §2: once, before live event handling.
4. `await indexer.start()` — spawn the drain loop.
5. `broadcaster` starts as today, then
   `database.set_on_commit(lambda: (broadcaster.notify(), indexer.notify()))`.

Teardown (reverse): restore `on_commit=None`, `await indexer.stop()`,
`index_conn.close()`, then the existing broadcaster/db teardown.

`app.state.indexer = indexer` for the endpoints.

Tests that never exercise search still pay the startup scan cost. It is
bounded — `_repos()` is empty when no work item has been created, so the
scan is a no-op in the majority of API tests.

---

## 7. Testing

Hermetic, no `claude`. New fixture helper in `tests/support/harness.py`:

```python
def make_repo_with_engineering(tmp_path, files: dict[str, str], name="sample") -> Path:
    # make_repo(), then write files under the repo, git add + commit, return path
```

| Test file | Covers |
|---|---|
| `tests/test_index_db.py` | migrate from empty; re-open is a no-op; `user_version` ahead → file rebuilt; FTS triggers created |
| `tests/test_index_ingest.py` | `split_front_matter` (present / absent / malformed); `kind` from folder; front-matter `kind:` override; `title` precedence; `scan_repo` finds nested `.md`, ignores non-`.md` and non-`.engineering`; `reconcile` insert/no-op/edit(hash change)/delete/rename(`git mv`, id stable); FTS row count tracks `documents` |
| `tests/test_search_api.py` | create work item → repo known; write `.engineering/specs/*.md` + `.engineering/plans/*.md`; `POST /index/rescan`; `GET /search?q=` ranks hits; `source_kind`/`kind`/`repo` filters; snippet present; `q` missing → 422; bad FTS → 422; `mode=vector` → 422; `GET /documents/{id}` full body; unknown → 404; `/health` index block |
| `tests/test_index_triggers.py` | `@pytest.mark.slow` — real uvicorn or in-process: `work_item_completed` (fake agent that writes an `.engineering/` file) → document appears without a manual rescan; startup scan picks up a pre-existing file |

Ruff clean, `pytest -m "not e2e"` green, frontend untouched.

---

## 8. Risks

- **Startup-scan cost in the API test suite.** Mitigated: no-op when no
  repos are known; `_repos()` is cheap otherwise. If it bites, tests can
  set `KRAFT_INDEX_REPOS=""` and the scan short-circuits.
- **FTS5 availability.** Bundled in CPython's `sqlite3` on the
  `python3.14` CI image and macOS; a startup `CREATE VIRTUAL TABLE …
  fts5` failure surfaces immediately as a clear error, not a silent
  degrade. No fallback path — FTS is the point of 4A.
- **`git ls-files` in a non-git dir** (a `KRAFT_INDEX_REPOS` path that is
  not a repo) → caught, logged, that repo skipped, scan continues.

---

## 9. Build order (→ implementation plan)

1. `index/db.py` + `RunDirs.index_db` + `test_index_db.py`
2. `index/ingest.py` (`scan_repo`, `split_front_matter`, `reconcile`) +
   `test_index_ingest.py` + `make_repo_with_engineering` helper
3. `index/service.py` `Indexer` (startup scan, `rescan_repo`, `search`,
   `get_document`) — no live loop yet
4. `api.py` — endpoints + lifespan (startup scan only) + `test_search_api.py`
5. `Indexer` live drain loop + `on_commit` multiplexer + `test_index_triggers.py`
6. `/health` index block + docs note in `04`'s build-order table (mark 4A done)
