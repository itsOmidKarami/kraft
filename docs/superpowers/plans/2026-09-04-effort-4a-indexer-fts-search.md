# Effort 4A — Indexer core + artifact ingestion + FTS search — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the orchestrator's search index — a second SQLite file that ingests `.engineering/**/*.md` artifacts across the repos the orchestrator knows, kept fresh by events + a startup scan, queryable over a FTS5 `/search` API.

**Architecture:** New `kraft/index/` package. `db.py` opens/migrates a disposable second SQLite file. `ingest.py` scans a repo with `git ls-files`, parses YAML front-matter, and reconciles the scan against `documents` rows with a content-hash diff (insert / rename / re-extract / delete). `service.py` hosts an `Indexer` that runs one startup scan, drains the orchestrator event bus for `work_item_completed` to trigger targeted repo rescans, and answers `search` / `get_document`. `api.py` exposes `GET /search`, `GET /documents/{id}`, `POST /index/rescan` and wires the `Indexer` into the FastAPI lifespan alongside the existing `Broadcaster`.

**Tech Stack:** Python 3.14, stdlib `sqlite3` (FTS5 built in), `pyyaml` (already a dep), `asyncio`, FastAPI. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-04-effort-4a-indexer-fts-search-design.md`

## Global Constraints

- Python `>=3.14`; no new runtime dependencies (spec §0, "Tech Stack").
- Index DB is a **second** SQLite file at `<KRAFT_RUN_DIR>/index.db`, never the state DB (spec §3).
- Index is **disposable**: on `user_version` ahead of code or unusable schema, delete the file and rebuild — no data-preserving migration (spec §2.4).
- Index DB is accessed by a **single `sqlite3.Connection` on the event-loop thread only**; blocking git scans go through `asyncio.to_thread`, the SQLite writes after them stay on-loop (spec §2.3). Mark this ceiling with a `ponytail:` comment.
- No poll loop. Triggers are: one startup scan, the event bus, `POST /index/rescan` (spec §2, §2.6).
- Live trigger set for 4A is **`work_item_completed` only** (spec §2.6).
- `source_kind` is the closed enum `('artifact', 'session_summary')`; 4A only ever writes `'artifact'` (spec §3).
- `kind` is an open tag: folder segment after `.engineering/`, front-matter `kind:` overrides, nullable (spec §2, §4.2).
- `INDEX_SCHEMA_VERSION = 1` (spec §3).
- No frontend changes. No `sqlite-vec`, no embeddings, no `document_chunks`, no session-summary ingestion, no `/work-items/{id}/documents` (spec §1 "Out").
- Follow existing patterns: pragmas and migration ladder shape from `src/kraft/db.py`; event-tail-drain shape from `src/kraft/ws.py`; test harness from `tests/support/harness.py`.
- Every task: `uv run ruff check .` and `uv run ruff format --check .` clean before commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/kraft/index/__init__.py` | empty package marker |
| `src/kraft/index/db.py` | `INDEX_SCHEMA_SQL`, `INDEX_SCHEMA_VERSION`, `connect()`, `open_index()` (migrate + disposable rebuild) |
| `src/kraft/index/ingest.py` | `ScannedDoc`, `ReconcileStats`, `split_front_matter()`, `derive_kind()`, `derive_title()`, `scan_repo()`, `reconcile()` |
| `src/kraft/index/service.py` | `Indexer` (startup scan, event drain loop, `rescan_repo`, `rescan_all`, `search`, `get_document`, `health`) |
| `src/kraft/paths.py` | +`RunDirs.index_db` property |
| `src/kraft/api.py` | +`GET /search`, +`GET /documents/{id}`, +`POST /index/rescan`, +`/health` index block, lifespan wiring |
| `tests/support/harness.py` | +`make_repo_with_engineering()` |
| `tests/test_index_db.py` | db.py |
| `tests/test_index_ingest.py` | ingest.py |
| `tests/test_search_api.py` | api endpoints + Indexer.search/get_document |
| `tests/test_index_triggers.py` | live event trigger + startup scan (`@pytest.mark.slow`) |

---

## Task 1: Index DB — schema, migrate, disposable rebuild

**Files:**
- Create: `src/kraft/index/__init__.py` (empty)
- Create: `src/kraft/index/db.py`
- Modify: `src/kraft/paths.py` (add `index_db` property after `db`)
- Test: `tests/test_index_db.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `kraft.index.db.INDEX_SCHEMA_VERSION: int` (== 1)
  - `kraft.index.db.connect(path: pathlib.Path) -> sqlite3.Connection` — pragmas + `row_factory = sqlite3.Row`
  - `kraft.index.db.open_index(path: pathlib.Path) -> sqlite3.Connection` — parent mkdir, connect, migrate; if `user_version` > code or schema unusable, `unlink` + recreate
  - `kraft.paths.RunDirs.index_db -> pathlib.Path` (== `self.base / "index.db"`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_index_db.py`:

```python
from __future__ import annotations

import sqlite3

import pytest

from kraft.index import db as index_db
from kraft.paths import RunDirs


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        r["name"]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }


def test_open_index_creates_schema_from_empty(tmp_path):
    conn = index_db.open_index(tmp_path / "index.db")
    try:
        assert {"documents", "document_links"} <= _tables(conn)
        (v,) = conn.execute("PRAGMA user_version").fetchone()
        assert v == index_db.INDEX_SCHEMA_VERSION
        # FTS5 virtual table + external-content triggers exist
        names = {
            r["name"]
            for r in conn.execute("SELECT name FROM sqlite_master").fetchall()
        }
        assert "documents_fts" in names
        assert {"documents_ai", "documents_ad", "documents_au"} <= names
    finally:
        conn.close()


def test_open_index_is_idempotent(tmp_path):
    p = tmp_path / "index.db"
    index_db.open_index(p).close()
    conn = index_db.open_index(p)  # must not raise, must not double-create
    try:
        (v,) = conn.execute("PRAGMA user_version").fetchone()
        assert v == index_db.INDEX_SCHEMA_VERSION
    finally:
        conn.close()


def test_open_index_rebuilds_when_version_ahead(tmp_path):
    p = tmp_path / "index.db"
    conn = index_db.open_index(p)
    conn.execute("INSERT INTO documents (id, repo, source_kind, kind, title, path, content, "
                 "content_hash, metadata_json, indexed_at) VALUES "
                 "('d1','/r','artifact','specs','T','.engineering/specs/a.md','body','h','{}','now')")
    conn.commit()
    conn.execute(f"PRAGMA user_version = {index_db.INDEX_SCHEMA_VERSION + 5}")
    conn.commit()
    conn.close()

    conn = index_db.open_index(p)  # sees a newer db -> nuke + recreate
    try:
        (v,) = conn.execute("PRAGMA user_version").fetchone()
        assert v == index_db.INDEX_SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    finally:
        conn.close()


def test_open_index_rebuilds_when_schema_corrupt(tmp_path):
    p = tmp_path / "index.db"
    p.write_bytes(b"this is not a sqlite database at all, not even the header")
    conn = index_db.open_index(p)
    try:
        assert "documents" in _tables(conn)
    finally:
        conn.close()


def test_rundirs_index_db_path(tmp_path):
    rd = RunDirs(tmp_path)
    assert rd.index_db == tmp_path / "index.db"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_db.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.index'`

- [ ] **Step 3: Add the `RunDirs.index_db` property**

In `src/kraft/paths.py`, add directly after the `db` property:

```python
    @property
    def index_db(self) -> Path:
        return self.base / "index.db"
```

- [ ] **Step 4: Create the package marker**

Create `src/kraft/index/__init__.py` as an empty file.

- [ ] **Step 5: Write `src/kraft/index/db.py`**

```python
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

INDEX_SCHEMA_VERSION = 1

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
"""

# ponytail: empty until 4B/4C add tables. Additive DDL only; a data-losing
# reshape just bumps INDEX_SCHEMA_VERSION and lets open_index() rebuild.
_MIGRATIONS: dict[int, list[str]] = {}


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _create_all(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN")
    # ponytail: naive ';' split — INDEX_SCHEMA_SQL has no embedded semicolons
    # outside the trigger bodies, which each end in ';\nEND'. Split on ';\n\n'
    # would be safer but the statements below are executed one-by-one after a
    # rstrip; sqlite executescript handles the triggers, so use it here.
    conn.executescript("COMMIT;" + INDEX_SCHEMA_SQL)
    conn.execute(f"PRAGMA user_version = {INDEX_SCHEMA_VERSION}")
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> bool:
    """Return True if the connection is usable at the current code version,
    False if the caller must rebuild the file."""
    try:
        (version,) = conn.execute("PRAGMA user_version").fetchone()
    except sqlite3.DatabaseError:
        return False
    if version == INDEX_SCHEMA_VERSION:
        return True
    if version > INDEX_SCHEMA_VERSION:
        logger.warning("index db v%d newer than code v%d; rebuilding", version, INDEX_SCHEMA_VERSION)
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


def open_index(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    if _migrate(conn):
        return conn
    conn.close()
    path.unlink(missing_ok=True)
    Path(f"{path}-wal").unlink(missing_ok=True)
    Path(f"{path}-shm").unlink(missing_ok=True)
    conn = connect(path)
    _create_all(conn)
    return conn
```

Note on `_create_all`: `conn.executescript` implicitly issues a `COMMIT`
first, so prefixing `"COMMIT;"` is defensive against the `BEGIN` above; if
ruff/readability prefers, drop the explicit `BEGIN`/prefix and just call
`conn.executescript(INDEX_SCHEMA_SQL)` then set `user_version`. Pick one
and keep it consistent. The tests only care about the end state.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_db.py -q`
Expected: PASS (5 tests)

- [ ] **Step 7: Lint**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean (run `uv run ruff format .` if needed, re-check)

- [ ] **Step 8: Commit**

```bash
git add src/kraft/index/__init__.py src/kraft/index/db.py src/kraft/paths.py tests/test_index_db.py
git commit -m "feat(index): disposable second SQLite file with FTS5 schema"
```

---

## Task 2: Artifact ingestion — scan a repo, reconcile against `documents`

**Files:**
- Create: `src/kraft/index/ingest.py`
- Modify: `tests/support/harness.py` (add `make_repo_with_engineering`)
- Test: `tests/test_index_ingest.py`

**Interfaces:**
- Consumes: `kraft.index.db` (a live connection for `reconcile`).
- Produces:
  - `kraft.index.ingest.ScannedDoc` — frozen dataclass: `path: str`, `kind: str | None`, `title: str`, `content: str`, `content_hash: str`, `metadata: dict`, `source_created_at: str | None`, `source_updated_at: str | None`
  - `kraft.index.ingest.ReconcileStats` — frozen dataclass: `inserted: int`, `updated: int`, `renamed: int`, `deleted: int`
  - `split_front_matter(text: str) -> tuple[dict, str]`
  - `derive_kind(path: str, front_matter: dict) -> str | None`
  - `derive_title(path: str, front_matter: dict, body: str) -> str`
  - `scan_repo(repo: pathlib.Path) -> list[ScannedDoc]`
  - `reconcile(conn: sqlite3.Connection, repo: str, scanned: list[ScannedDoc], *, source_kind: str = "artifact", now: str | None = None) -> ReconcileStats`
  - `tests.support.harness.make_repo_with_engineering(tmp_path, files: dict[str, str], name: str = "sample") -> pathlib.Path`

- [ ] **Step 1: Add the test harness helper**

In `tests/support/harness.py`, after `make_repo`:

```python
def make_repo_with_engineering(
    tmp_path: Path, files: dict[str, str], name: str = "sample"
) -> Path:
    """make_repo(), then add repo-relative `files` (path -> text), commit, return the repo."""
    dest = make_repo(tmp_path, name)
    for rel, text in files.items():
        fp = dest / rel
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(text)
    _git(dest, "add", "-A")
    _git(dest, "commit", "-m", "add engineering docs")
    return dest
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_index_ingest.py`:

```python
from __future__ import annotations

import subprocess

import pytest
from support.harness import make_repo_with_engineering

from kraft.index import db as index_db
from kraft.index import ingest


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


# ---- split_front_matter ----

def test_split_front_matter_present():
    fm, body = ingest.split_front_matter("---\ntitle: Hi\nkind: notes\n---\n# Body\ntext\n")
    assert fm == {"title": "Hi", "kind": "notes"}
    assert body == "# Body\ntext\n"


def test_split_front_matter_absent():
    fm, body = ingest.split_front_matter("# Just a heading\nbody")
    assert fm == {}
    assert body == "# Just a heading\nbody"


def test_split_front_matter_malformed_yaml_is_ignored():
    text = "---\n: : not valid : :\n---\nbody\n"
    fm, body = ingest.split_front_matter(text)
    assert fm == {}
    assert body == "body\n"


# ---- derive_kind / derive_title ----

def test_derive_kind_from_folder():
    assert ingest.derive_kind(".engineering/specs/a.md", {}) == "specs"
    assert ingest.derive_kind(".engineering/a.md", {}) is None


def test_derive_kind_front_matter_overrides():
    assert ingest.derive_kind(".engineering/specs/a.md", {"kind": "adr"}) == "adr"


def test_derive_title_precedence():
    assert ingest.derive_title("x/a.md", {"title": "FM"}, "# Heading\n") == "FM"
    assert ingest.derive_title("x/a.md", {}, "\n#  Heading here \nmore") == "Heading here"
    assert ingest.derive_title("x/my-doc.md", {}, "no heading") == "my-doc"


# ---- scan_repo ----

def test_scan_repo_finds_nested_md_ignores_others():
    repo = make_repo_with_engineering(
        tmp_path_factory_marker := None or _tp(),
        {
            ".engineering/specs/one.md": "---\ntitle: One\n---\nalpha bravo\n",
            ".engineering/plans/two.md": "# Two\ncharlie delta\n",
            ".engineering/notes.txt": "ignored, not md",
            "README.md": "ignored, not under .engineering",
        },
    )
    docs = {d.path: d for d in ingest.scan_repo(repo)}
    assert set(docs) == {".engineering/specs/one.md", ".engineering/plans/two.md"}
    assert docs[".engineering/specs/one.md"].kind == "specs"
    assert docs[".engineering/specs/one.md"].title == "One"
    assert docs[".engineering/plans/two.md"].kind == "plans"
    assert docs[".engineering/plans/two.md"].title == "Two"
    assert docs[".engineering/specs/one.md"].content_hash != docs[".engineering/plans/two.md"].content_hash
    assert docs[".engineering/specs/one.md"].source_updated_at  # git timestamp present


def test_scan_repo_non_git_dir_returns_empty(tmp_path):
    (tmp_path / "plain").mkdir()
    assert ingest.scan_repo(tmp_path / "plain") == []


# ---- reconcile ----

@pytest.fixture
def conn(tmp_path):
    c = index_db.open_index(tmp_path / "index.db")
    yield c
    c.close()


def _rows(conn, repo):
    return {
        r["path"]: (r["id"], r["content_hash"], r["title"], r["kind"])
        for r in conn.execute("SELECT * FROM documents WHERE repo=?", (repo,)).fetchall()
    }


def test_reconcile_insert_then_noop(conn):
    repo = make_repo_with_engineering(_tp(), {".engineering/specs/a.md": "# A\nalpha\n"})
    s1 = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s1.inserted, s1.updated, s1.renamed, s1.deleted) == (1, 0, 0, 0)
    assert conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0] == 1

    s2 = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s2.inserted, s2.updated, s2.renamed, s2.deleted) == (0, 0, 0, 0)


def test_reconcile_edit_changes_hash_and_reextracts(conn):
    repo = make_repo_with_engineering(_tp(), {".engineering/specs/a.md": "# A\nalpha\n"})
    ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    doc_id = _rows(conn, str(repo))[".engineering/specs/a.md"][0]

    (repo / ".engineering/specs/a.md").write_text("# A\nalpha bravo charlie\n")
    _git(repo, "commit", "-am", "edit")
    s = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s.inserted, s.updated, s.renamed, s.deleted) == (0, 1, 0, 0)
    assert _rows(conn, str(repo))[".engineering/specs/a.md"][0] == doc_id  # id stable
    assert conn.execute(
        "SELECT content FROM documents WHERE id=?", (doc_id,)
    ).fetchone()["content"] == "# A\nalpha bravo charlie\n"


def test_reconcile_rename_keeps_id(conn):
    repo = make_repo_with_engineering(_tp(), {".engineering/specs/a.md": "# A\nunique body xyzzy\n"})
    ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    doc_id = _rows(conn, str(repo))[".engineering/specs/a.md"][0]

    _git(repo, "mv", ".engineering/specs/a.md", ".engineering/specs/renamed.md")
    _git(repo, "commit", "-m", "rename")
    s = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s.inserted, s.updated, s.renamed, s.deleted) == (0, 0, 1, 0)
    rows = _rows(conn, str(repo))
    assert ".engineering/specs/a.md" not in rows
    assert rows[".engineering/specs/renamed.md"][0] == doc_id


def test_reconcile_delete_removes_row_and_fts(conn):
    repo = make_repo_with_engineering(
        _tp(),
        {
            ".engineering/specs/a.md": "# A\nkeep me\n",
            ".engineering/specs/b.md": "# B\ndelete me\n",
        },
    )
    ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    _git(repo, "rm", ".engineering/specs/b.md")
    _git(repo, "commit", "-m", "rm b")
    s = ingest.reconcile(conn, str(repo), ingest.scan_repo(repo))
    assert (s.inserted, s.updated, s.renamed, s.deleted) == (0, 0, 0, 1)
    assert set(_rows(conn, str(repo))) == {".engineering/specs/a.md"}
    assert conn.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0] == 1


# a tmp dir per call — pytest tmp_path is function-scoped; several helpers above
# need distinct repos within one test, so mint fresh dirs.
import tempfile


def _tp():
    return __import__("pathlib").Path(tempfile.mkdtemp())
```

> Executor note: the `_tp()` / `tmp_path_factory_marker` shim above is ugly.
> Prefer converting these to use pytest's `tmp_path_factory` fixture
> (`tmp_path_factory.mktemp("repo")`). Rewrite the test signatures to take
> `tmp_path_factory` and drop `_tp()`. Keep the assertions identical.

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_ingest.py -q`
Expected: FAIL — `AttributeError: module 'kraft.index.ingest' has no attribute ...` / `ModuleNotFoundError`

- [ ] **Step 4: Write `src/kraft/index/ingest.py`**

```python
from __future__ import annotations

import hashlib
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)
_ATX_HEADING = re.compile(r"^\s*#\s+(.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class ScannedDoc:
    path: str
    kind: str | None
    title: str
    content: str
    content_hash: str
    metadata: dict
    source_created_at: str | None
    source_updated_at: str | None


@dataclass(frozen=True)
class ReconcileStats:
    inserted: int = 0
    updated: int = 0
    renamed: int = 0
    deleted: int = 0


def split_front_matter(text: str) -> tuple[dict, str]:
    m = _FRONT_MATTER.match(text)
    if not m:
        return {}, text
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}, text[m.end() :]
    if not isinstance(data, dict):
        return {}, text[m.end() :]
    return data, text[m.end() :]


def derive_kind(path: str, front_matter: dict) -> str | None:
    fm = front_matter.get("kind")
    if isinstance(fm, str) and fm:
        return fm
    parts = Path(path).parts
    if "engineering" in "".join(parts):  # cheap guard; real check below
        pass
    try:
        i = parts.index(".engineering")
    except ValueError:
        return None
    rest = parts[i + 1 :]
    return rest[0] if len(rest) > 1 else None


def derive_title(path: str, front_matter: dict, body: str) -> str:
    fm = front_matter.get("title")
    if isinstance(fm, str) and fm.strip():
        return fm.strip()
    m = _ATX_HEADING.search(body)
    if m:
        return m.group(1).strip()
    return Path(path).stem


def _git_lines(repo: Path, *args: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True
    )
    if out.returncode != 0:
        return []
    return out.stdout.splitlines()


def _git_timestamps(repo: Path, rel: str) -> tuple[str | None, str | None]:
    updated = _git_lines(repo, "log", "-1", "--format=%cI", "--", rel)
    created = _git_lines(
        repo, "log", "--diff-filter=A", "--follow", "--format=%cI", "--", rel
    )
    return (created[-1] if created else None, updated[0] if updated else None)


def scan_repo(repo: Path) -> list[ScannedDoc]:
    repo = Path(repo)
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--", ".engineering/"],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return []
    rels = [r for r in out.stdout.split("\0") if r.endswith(".md")]
    docs: list[ScannedDoc] = []
    for rel in rels:
        try:
            text = (repo / rel).read_text()
        except OSError:
            continue
        fm, body = split_front_matter(text)
        created, updated = _git_timestamps(repo, rel)
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
            )
        )
    return docs


def reconcile(
    conn,
    repo: str,
    scanned: list[ScannedDoc],
    *,
    source_kind: str = "artifact",
    now: str | None = None,
) -> ReconcileStats:
    from kraft.events import _now as _clock

    now = now or _clock()
    existing = {
        r["path"]: (r["id"], r["content_hash"])
        for r in conn.execute(
            "SELECT id, path, content_hash FROM documents WHERE repo=? AND source_kind=?",
            (repo, source_kind),
        ).fetchall()
    }
    by_path = {d.path: d for d in scanned}

    new_paths = [p for p in by_path if p not in existing]
    gone_paths = [p for p in existing if p not in by_path]
    gone_by_hash = {existing[p][1]: p for p in gone_paths}

    ins = upd = ren = dele = 0
    conn.execute("BEGIN")
    try:
        # renames first: a new path whose hash matches a vanished path
        renamed_from: set[str] = set()
        for p in list(new_paths):
            d = by_path[p]
            src = gone_by_hash.get(d.content_hash)
            if src is not None and src not in renamed_from:
                conn.execute(
                    "UPDATE documents SET path=?, title=?, kind=?, metadata_json=?, "
                    "source_created_at=?, source_updated_at=?, indexed_at=? "
                    "WHERE id=?",
                    (
                        d.path, d.title, d.kind, _json(d.metadata),
                        d.source_created_at, d.source_updated_at, now,
                        existing[src][0],
                    ),
                )
                renamed_from.add(src)
                new_paths.remove(p)
                ren += 1

        # genuine inserts
        for p in new_paths:
            d = by_path[p]
            conn.execute(
                "INSERT INTO documents (id, repo, source_kind, kind, title, path, content, "
                "content_hash, metadata_json, source_created_at, source_updated_at, indexed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex, repo, source_kind, d.kind, d.title, d.path, d.content,
                    d.content_hash, _json(d.metadata), d.source_created_at,
                    d.source_updated_at, now,
                ),
            )
            ins += 1

        # deletes: vanished and not consumed by a rename
        for src in gone_paths:
            if src in renamed_from:
                continue
            conn.execute("DELETE FROM documents WHERE id=?", (existing[src][0],))
            dele += 1

        # same path, changed hash -> re-extract
        for p, d in by_path.items():
            if p in existing and existing[p][1] != d.content_hash:
                conn.execute(
                    "UPDATE documents SET title=?, kind=?, content=?, content_hash=?, "
                    "metadata_json=?, source_created_at=?, source_updated_at=?, indexed_at=? "
                    "WHERE id=?",
                    (
                        d.title, d.kind, d.content, d.content_hash, _json(d.metadata),
                        d.source_created_at, d.source_updated_at, now, existing[p][0],
                    ),
                )
                upd += 1
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return ReconcileStats(inserted=ins, updated=upd, renamed=ren, deleted=dele)


def _json(d: dict) -> str:
    import json

    return json.dumps(d, sort_keys=True)
```

> Executor note: clean up `derive_kind` — the `if "engineering" in ...: pass`
> block is dead scaffolding; delete it, keep the `parts.index(".engineering")`
> logic. `_json` / inline `import json` should move to a module-level
> `import json`. Keep behaviour identical; ruff must pass.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_ingest.py -q`
Expected: PASS

- [ ] **Step 6: Lint + full suite spot check**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest tests/test_index_db.py tests/test_index_ingest.py -q`
Expected: clean + PASS

- [ ] **Step 7: Commit**

```bash
git add src/kraft/index/ingest.py tests/support/harness.py tests/test_index_ingest.py
git commit -m "feat(index): git-scan .engineering artifacts + content-hash reconcile"
```

---

## Task 3: `Indexer` service — startup scan, rescan, search, get_document

**Files:**
- Create: `src/kraft/index/service.py`
- Test: extend `tests/test_index_ingest.py` is wrong — create `tests/test_index_service.py`

**Interfaces:**
- Consumes: `kraft.index.db.open_index`, `kraft.index.ingest.{scan_repo,reconcile,ReconcileStats}`, a state `Database` (for `read`).
- Produces:
  - `kraft.index.service.Indexer(index_conn: sqlite3.Connection, state_db, *, repos_env: str | None = None)`
  - `Indexer.repos() -> list[str]` — sorted distinct `work_items.repo` ∪ `KRAFT_INDEX_REPOS` split on `os.pathsep`, existing dirs only
  - `async Indexer.rescan_repo(repo: str) -> ReconcileStats`
  - `async Indexer.rescan_all() -> dict[str, ReconcileStats]`
  - `async Indexer.startup_scan() -> None` — bounded `asyncio.gather` (cap 4) over `repos()`
  - `Indexer.search(q: str, *, source_kind: str | None = None, kind: str | None = None, repo: str | None = None, limit: int = 20) -> list[dict]` — raises `sqlite3.OperationalError` on a malformed FTS query
  - `Indexer.get_document(doc_id: str) -> dict | None`
  - `Indexer.health() -> dict` — `{"last_scan_at", "repos_scanned", "documents", "errors"}`
  - live-loop methods added in Task 5 (`notify`, `start`, `stop`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_index_service.py`:

```python
from __future__ import annotations

import asyncio

import pytest
from support.harness import make_repo_with_engineering

from kraft.db import Database
from kraft.index import db as index_db
from kraft.index.service import Indexer


async def _state_db(tmp_path):
    return await Database.open(tmp_path / "state.db")


def _seed_work_item(db, repo: str):
    return db.write(
        lambda c: c.execute(
            "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
            "chain_definition, status, created_at, updated_at) VALUES "
            "('w1','b1','t',?,'quick-task','{}','active','now','now')",
            (repo,),
        )
    )


def test_repos_union_of_work_items_and_env(tmp_path, monkeypatch):
    async def scenario():
        state = await _state_db(tmp_path)
        try:
            repo_a = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nx\n"}, "a")
            await _seed_work_item(state, str(repo_a))
            repo_b = make_repo_with_engineering(tmp_path, {".engineering/specs/b.md": "# B\ny\n"}, "b")
            monkeypatch.setenv("KRAFT_INDEX_REPOS", f"{repo_b}{__import__('os').pathsep}/no/such/dir")
            conn = index_db.open_index(tmp_path / "index.db")
            try:
                ix = Indexer(conn, state, repos_env=f"{repo_b}:/no/such/dir")
                assert set(ix.repos()) == {str(repo_a), str(repo_b)}
            finally:
                conn.close()
        finally:
            await state.close()

    asyncio.run(scenario())


def test_startup_scan_then_search(tmp_path):
    async def scenario():
        state = await _state_db(tmp_path)
        try:
            repo = make_repo_with_engineering(
                tmp_path,
                {
                    ".engineering/specs/ws.md": "---\ntitle: WS transport\n---\nreconnect backoff schedule\n",
                    ".engineering/plans/ui.md": "# UI plan\nboard and detail view\n",
                },
            )
            await _seed_work_item(state, str(repo))
            conn = index_db.open_index(tmp_path / "index.db")
            try:
                ix = Indexer(conn, state)
                await ix.startup_scan()

                hits = ix.search("reconnect backoff")
                assert [h["path"] for h in hits] == [".engineering/specs/ws.md"]
                assert hits[0]["title"] == "WS transport"
                assert hits[0]["kind"] == "specs"
                assert "[reconnect]" in hits[0]["snippet"]
                assert hits[0]["links"] == []

                assert ix.search("board", kind="plans")
                assert ix.search("board", kind="specs") == []
                assert ix.search("backoff", repo="/other/repo") == []

                doc = ix.get_document(hits[0]["id"])
                assert doc["content"] == "reconnect backoff schedule\n"
                assert doc["metadata"] == {}
                assert ix.get_document("nope") is None

                h = ix.health()
                assert h["documents"] == 2 and h["repos_scanned"] == 1
            finally:
                conn.close()
        finally:
            await state.close()

    asyncio.run(scenario())


def test_search_malformed_query_raises(tmp_path):
    async def scenario():
        state = await _state_db(tmp_path)
        try:
            conn = index_db.open_index(tmp_path / "index.db")
            try:
                ix = Indexer(conn, state)
                with pytest.raises(Exception):
                    ix.search('"unterminated')
            finally:
                conn.close()
        finally:
            await state.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_index_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'kraft.index.service'`

- [ ] **Step 3: Write `src/kraft/index/service.py`**

```python
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from kraft.events import _now
from kraft.index import ingest

logger = logging.getLogger(__name__)

_SCAN_FANOUT = 4


class Indexer:
    def __init__(self, index_conn, state_db, *, repos_env: str | None = None) -> None:
        self._conn = index_conn
        self._state = state_db
        self._repos_env = repos_env if repos_env is not None else os.environ.get("KRAFT_INDEX_REPOS")
        self._last_scan_at: str | None = None
        self._repos_scanned: int = 0
        self._errors: list[str] = []

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
        # index connection. Fine while ingestion is small + low-QPS; move behind
        # a writer queue if a large-repo scan ever stalls the loop.
        stats = ingest.reconcile(self._conn, repo, scanned)
        return stats

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

    async def startup_scan(self) -> None:
        self._errors = []
        await self.rescan_all()

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
                "id": r["id"], "repo": r["repo"], "kind": r["kind"],
                "source_kind": r["source_kind"], "title": r["title"], "path": r["path"],
                "snippet": r["snippet"], "score": r["score"], "links": [],
            }
            for r in rows
        ]

    def get_document(self, doc_id: str) -> dict | None:
        r = self._conn.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if r is None:
            return None
        return {
            "id": r["id"], "repo": r["repo"], "source_kind": r["source_kind"],
            "kind": r["kind"], "title": r["title"], "path": r["path"],
            "content": r["content"], "metadata": json.loads(r["metadata_json"]),
            "source_created_at": r["source_created_at"],
            "source_updated_at": r["source_updated_at"], "indexed_at": r["indexed_at"],
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_service.py -q`
Expected: PASS (3 tests)

- [ ] **Step 5: Lint**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean

- [ ] **Step 6: Commit**

```bash
git add src/kraft/index/service.py tests/test_index_service.py
git commit -m "feat(index): Indexer service — startup scan, rescan, FTS search"
```

---

## Task 4: API endpoints + lifespan wiring (startup scan only)

**Files:**
- Modify: `src/kraft/api.py`
- Test: `tests/test_search_api.py`

**Interfaces:**
- Consumes: `kraft.index.db.open_index`, `kraft.index.service.Indexer`, `RunDirs.index_db`.
- Produces (HTTP): `GET /search`, `GET /documents/{id}`, `POST /index/rescan`; `/health` gains an `"index"` key.
- Produces (state): `app.state.indexer: Indexer`, `app.state.index_conn: sqlite3.Connection`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_search_api.py`:

```python
from __future__ import annotations

from pathlib import Path

import pytest
from support.harness import isolated_bd, fake_templates_dir, make_repo_with_engineering

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FAKE_CLAUDE = _REPO_ROOT / "fixtures" / "fake-claude.sh"


def _client(tmp_path, monkeypatch, *, index_repos: str | None = None):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("KRAFT_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("KRAFT_BD_CWD", str(isolated_bd(tmp_path)))
    monkeypatch.setenv("KRAFT_TEMPLATES_DIR", str(fake_templates_dir(tmp_path, str(_FAKE_CLAUDE))))
    monkeypatch.setenv("KRAFT_FRONTEND_DIST", str(tmp_path / "no-dist"))
    if index_repos is not None:
        monkeypatch.setenv("KRAFT_INDEX_REPOS", index_repos)
    import kraft.api as api

    return TestClient(api.app)


def test_search_documents_and_rescan(tmp_path, monkeypatch):
    repo = make_repo_with_engineering(
        tmp_path,
        {
            ".engineering/specs/ws.md": "---\ntitle: WS transport\nowner: omid\n---\nreconnect backoff schedule caps\n",
            ".engineering/plans/ui.md": "# UI plan\nboard and detail view\n",
        },
    )
    with _client(tmp_path, monkeypatch, index_repos=str(repo)) as client:
        # startup scan already ran against KRAFT_INDEX_REPOS
        r = client.get("/search", params={"q": "reconnect backoff"})
        assert r.status_code == 200
        body = r.json()
        assert body["mode"] == "fts"
        assert [h["path"] for h in body["results"]] == [".engineering/specs/ws.md"]
        hit = body["results"][0]
        assert hit["title"] == "WS transport"
        assert hit["kind"] == "specs"
        assert "[reconnect]" in hit["snippet"]
        assert hit["links"] == []

        assert client.get("/search", params={"q": "board", "kind": "plans"}).json()["results"]
        assert not client.get("/search", params={"q": "board", "kind": "specs"}).json()["results"]

        # documents/{id}
        doc = client.get(f"/documents/{hit['id']}").json()
        assert doc["content"] == "reconnect backoff schedule caps\n"
        assert doc["metadata"] == {"owner": "omid"}
        assert client.get("/documents/nope").status_code == 404

        # rescan endpoint
        (repo / ".engineering/specs/new.md").write_text("# New\nfresh material here\n")
        import subprocess
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "new"], check=True, capture_output=True)
        rr = client.post("/index/rescan", params={"repo": str(repo)})
        assert rr.status_code == 200
        assert rr.json()["stats"]["inserted"] == 1
        assert client.get("/search", params={"q": "fresh material"}).json()["results"]


def test_search_validation(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.get("/search", params={"q": ""}).status_code == 422
        assert client.get("/search").status_code == 422
        assert client.get("/search", params={"q": "x", "mode": "vector"}).status_code == 422
        assert client.get("/search", params={"q": '"unterminated'}).status_code == 422


def test_rescan_unknown_repo_404(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        assert client.post("/index/rescan", params={"repo": "/not/known"}).status_code == 404


def test_health_has_index_block(tmp_path, monkeypatch):
    with _client(tmp_path, monkeypatch) as client:
        h = client.get("/health").json()
        assert "index" in h
        assert set(h["index"]) == {"last_scan_at", "repos_scanned", "documents", "errors"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_search_api.py -q`
Expected: FAIL — 404s on `/search` (routes absent), `KeyError: 'index'`

- [ ] **Step 3: Wire the lifespan**

In `src/kraft/api.py`:

Add imports near the top (with the other `from kraft...` imports):

```python
from kraft.index import db as index_db
from kraft.index.service import Indexer
```

In `lifespan`, after the `app.state.reattach_summary = summary` block and
before the `dist = ...` block, add:

```python
    index_conn = index_db.open_index(run_dirs.index_db)
    indexer = Indexer(index_conn, database, repos_env=os.environ.get("KRAFT_INDEX_REPOS"))
    await indexer.startup_scan()
    app.state.index_conn = index_conn
    app.state.indexer = indexer
```

In the `finally:` teardown block, before `await database.close()`:

```python
        index_conn.close()
```

- [ ] **Step 4: Add the endpoints**

In `src/kraft/api.py`, add after the `get_log` handler (before the
`_LOCAL_HOSTS` block) — except `/health` edit which is in Step 5:

```python
@app.get("/search")
async def search(
    request: Request,
    q: str = "",
    source_kind: str | None = None,
    kind: str | None = None,
    repo: str | None = None,
    mode: str = "fts",
    limit: int = 20,
):
    if not q.strip():
        raise HTTPException(422, "q is required")
    if mode != "fts":
        raise HTTPException(422, "only mode=fts is supported in this build")
    limit = max(1, min(limit, 100))
    ix = request.app.state.indexer
    try:
        results = ix.search(
            q, source_kind=source_kind, kind=kind, repo=repo, limit=limit
        )
    except sqlite3.OperationalError as exc:
        raise HTTPException(422, f"bad search query: {exc}") from exc
    return {"query": q, "mode": mode, "results": results}


@app.get("/documents/{doc_id}")
async def get_document(doc_id: str, request: Request):
    doc = request.app.state.indexer.get_document(doc_id)
    if doc is None:
        raise HTTPException(404, "unknown document")
    return doc


@app.post("/index/rescan")
async def index_rescan(request: Request, repo: str | None = None):
    ix = request.app.state.indexer
    if repo is not None:
        if repo not in ix.repos():
            raise HTTPException(404, f"unknown repo: {repo}")
        stats = await ix.rescan_repo(repo)
        return {"repo": repo, "stats": stats.__dict__}
    allstats = await ix.rescan_all()
    return {
        "repo": None,
        "stats": {
            "inserted": sum(s.inserted for s in allstats.values()),
            "updated": sum(s.updated for s in allstats.values()),
            "renamed": sum(s.renamed for s in allstats.values()),
            "deleted": sum(s.deleted for s in allstats.values()),
        },
    }
```

Add `import sqlite3` to the top-of-file imports (stdlib group).

- [ ] **Step 5: Extend `/health`**

In the `health` handler, add `"index": request.app.state.indexer.health()`
to the returned dict.

- [ ] **Step 6: Run tests**

Run: `uv run pytest tests/test_search_api.py -q`
Expected: PASS

- [ ] **Step 7: Full suite + lint (middleware/lifespan is global — check nothing else broke)**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not e2e" -q`
Expected: clean + all PASS (prior count + the new tests)

- [ ] **Step 8: Commit**

```bash
git add src/kraft/api.py tests/test_search_api.py
git commit -m "feat(api): GET /search, GET /documents/{id}, POST /index/rescan"
```

---

## Task 5: Live event trigger — drain the bus, rescan on `work_item_completed`

**Files:**
- Modify: `src/kraft/index/service.py` (add `notify` / `start` / `stop` / `_run`)
- Modify: `src/kraft/api.py` (multiplex `on_commit`, start/stop the loop)
- Test: `tests/test_index_triggers.py`

**Interfaces:**
- Consumes: `kraft.events.read_after`, the state `Database`.
- Produces:
  - `Indexer.notify() -> None` — set an `asyncio.Event`
  - `async Indexer.start() -> None` — spawn `_run`; snapshots the current `events` max seq as the starting cursor
  - `async Indexer.stop() -> None` — cancel + await `_run`
  - `_run` behaviour: on wake, `events.read_after(cursor)`, advance cursor past all, collect repos of `work_item_completed` events, `await rescan_repo` for each distinct repo

- [ ] **Step 1: Write the failing test**

Create `tests/test_index_triggers.py`:

```python
from __future__ import annotations

import asyncio

import pytest
from support.harness import make_repo_with_engineering

from kraft.db import Database
from kraft.index import db as index_db
from kraft.index.service import Indexer

pytestmark = pytest.mark.slow


def test_work_item_completed_triggers_repo_rescan(tmp_path):
    async def scenario():
        state = await Database.open(tmp_path / "state.db")
        conn = index_db.open_index(tmp_path / "index.db")
        try:
            repo = make_repo_with_engineering(tmp_path, {".engineering/specs/a.md": "# A\nseed\n"})
            await state.write(
                lambda c: c.execute(
                    "INSERT INTO work_items (id, bead_id, title, repo, chain_template, "
                    "chain_definition, status, created_at, updated_at) VALUES "
                    "('w1','b1','t',?,'quick-task','{}','active','now','now')",
                    (str(repo),),
                )
            )
            ix = Indexer(conn, state)
            await ix.startup_scan()
            assert ix.health()["documents"] == 1

            await ix.start()
            state.set_on_commit(ix.notify)
            try:
                # a new artifact lands, then the completion event fires
                (repo / ".engineering/specs/b.md").write_text("# B\nlate breaking\n")
                import subprocess
                subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
                subprocess.run(["git", "-C", str(repo), "commit", "-m", "b"], check=True, capture_output=True)

                await state.write(
                    lambda c: c.execute(
                        "INSERT INTO events (work_item_id, type, payload, created_at) "
                        "VALUES ('w1','work_item_completed','{}','now')"
                    )
                )
                for _ in range(50):
                    if ix.search("late breaking"):
                        break
                    await asyncio.sleep(0.1)
                assert ix.search("late breaking")
            finally:
                state.set_on_commit(None)
                await ix.stop()
        finally:
            conn.close()
            await state.close()

    asyncio.run(scenario())
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_index_triggers.py -q`
Expected: FAIL — `AttributeError: 'Indexer' object has no attribute 'start'`

- [ ] **Step 3: Add the live loop to `Indexer`**

In `src/kraft/index/service.py`, add to `__init__`:

```python
        self._cursor: int = 0
        self._wakeup = asyncio.Event()
        self._task: asyncio.Task | None = None
```

Add methods:

```python
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
        from kraft import events

        while True:
            try:
                await self._wakeup.wait()
                self._wakeup.clear()
                new = self._state.read(lambda c: events.read_after(c, self._cursor))
                repos: list[str] = []
                for ev in new:
                    self._cursor = ev["seq"]
                    if ev["type"] == "work_item_completed":
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
```

- [ ] **Step 4: Run the trigger test**

Run: `uv run pytest tests/test_index_triggers.py -q`
Expected: PASS

- [ ] **Step 5: Multiplex `on_commit` in the API**

In `src/kraft/api.py` `lifespan`, replace:

```python
    database.set_on_commit(broadcaster.notify)
```

with:

```python
    await indexer.start()
    database.set_on_commit(lambda: (broadcaster.notify(), indexer.notify()))
```

In the `finally:` teardown, before `await broadcaster.stop()`:

```python
        await indexer.stop()
```

(keep the existing `database.set_on_commit(None)` first line of `finally`).

- [ ] **Step 6: Full suite + lint**

Run: `uv run ruff check . && uv run ruff format --check . && uv run pytest -m "not e2e" -q`
Expected: clean + all PASS

- [ ] **Step 7: Commit**

```bash
git add src/kraft/index/service.py src/kraft/api.py tests/test_index_triggers.py
git commit -m "feat(index): live rescan on work_item_completed via on_commit bus"
```

---

## Task 6: Docs — mark 4A done in the consolidated build order

**Files:**
- Modify: `docs/consolidated/04_indexer_search.md` (§11 build order)
- Modify: `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md` (§3 item 4)

**Interfaces:** none (docs only).

- [ ] **Step 1: Annotate doc 04 §11**

In `docs/consolidated/04_indexer_search.md`, in the `## 11. Build Order`
list, mark steps 1/2/3/5 as shipped in Effort 4A and note 4B/4C:

```markdown
1. ✅ **Schema + migrations** (Effort 4A) — `documents`, `documents_fts`,
   `document_links` (links table created, populated in 4B).
2. ✅ **Artifact ingestion** (Effort 4A) — git scan, front-matter parse,
   content-hash diff (§4, §5).
3. ✅ **Event-bus subscription + startup scan** (Effort 4A) — `work_item_completed`
   trigger + one startup scan, no poll loop (§2).
4. **Session-summary ingestion + node/task linkage** (Effort 4B, Kraft-bj9.2) —
   blocked on the Execution Worker writing `.engineering/sessions/` +
   `session_summary_ref`.
5. ✅ **FTS5 `/search` endpoint, text-only** (Effort 4A) — plus `GET /documents/{id}`,
   `POST /index/rescan`.
6. **`sqlite-vec` + local embedding + chunking + hybrid ranking** (Effort 4C,
   Kraft-bj9.3) — deferred, last.
```

- [ ] **Step 2: Update the master plan §3**

In `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md`
§3, change item 4 from:

```markdown
4. Indexer + search (`04`)
```

to:

```markdown
4. Indexer + search (`04`) — **4A shipped** (index DB + `.engineering/`
   artifact ingestion + event/startup triggers + FTS `/search` /
   `/documents/{id}` / `/index/rescan`). 4B (session summaries, blocked on
   Execution Worker) and 4C (`sqlite-vec` + embeddings) tracked as
   Kraft-bj9.2 / Kraft-bj9.3.
```

- [ ] **Step 3: Commit**

```bash
git add docs/consolidated/04_indexer_search.md docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md
git commit -m "docs: mark Effort 4A shipped in build order + master plan"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1.1 index DB (documents/fts/links, disposable) | Task 1 |
| §1.2 artifact ingestion (scan, front-matter, kind, hash diff) | Task 2 |
| §1.3 triggers (startup + bus + rescan) | Task 3 (startup), Task 4 (rescan endpoint), Task 5 (bus) |
| §1.5 `/search`, `/documents/{id}`, `/index/rescan` | Task 4 |
| §2.2 connected-repo set (`work_items.repo` ∪ `KRAFT_INDEX_REPOS`) | Task 3 (`repos()`) |
| §2.3 single on-loop conn + `to_thread` scans + ceiling comment | Task 3 (`rescan_repo`) |
| §2.4 disposable rebuild | Task 1 (`open_index`) |
| §2.5 `on_commit` multiplexer | Task 5 |
| §2.6 `work_item_completed` only | Task 5 (`_run`) |
| §2.7 title derivation | Task 2 (`derive_title`) |
| §2.8 snippet | Task 3 (`search` SQL) |
| §2.9 no-`q` → 422 | Task 4 (`search` handler) |
| §3 schema (exact DDL) | Task 1 (`INDEX_SCHEMA_SQL`) |
| §5 API shapes (search/doc/rescan/health) | Task 4 |
| §6 lifespan order | Task 4 + Task 5 |
| §7 test files | all tasks; `test_index_triggers` slow-marked in Task 5 |
| §1 "Out" — no vec/sessions/UI/`/work-items/{id}/documents` | not implemented (correct) |
| §9 build order (6 sub-steps) | Tasks 1–6 |

No gaps.

**Placeholder scan:** two "Executor note" blocks in Tasks 2 flag scaffolding
to clean up (`_tp()` shim → `tmp_path_factory`; dead `if "engineering"` line;
inline `import json` → module level). These are explicit cleanup
instructions with the target state named, not "TBD". All code steps carry
real code. No "add error handling" hand-waves — the 422/404 paths are
written out.

**Type consistency:**
- `ReconcileStats` fields `inserted/updated/renamed/deleted` — same in Task 2
  definition, Task 4 `stats.__dict__` / sum expressions, Task 4 test
  assertions. ✔
- `Indexer.__init__(index_conn, state_db, *, repos_env=None)` — same in Task 3
  and Task 4 lifespan call. ✔
- `search()` returns dicts with `id/repo/kind/source_kind/title/path/snippet/score/links`
  — Task 3 definition matches Task 3 + Task 4 test assertions. ✔
- `get_document()` returns `content/metadata/links/...` — Task 3 matches
  Task 4 test (`doc["content"]`, `doc["metadata"]`). ✔
- `open_index` / `connect` / `INDEX_SCHEMA_VERSION` names — Task 1 defines,
  Tasks 3/4/5 tests import them consistently. ✔
- `make_repo_with_engineering(tmp_path, files, name=...)` — Task 2 defines,
  Tasks 3/4/5 tests call with that signature. ✔

**Execution:** Inline (see handoff note) — tasks share one new package and
one dev holding the whole design in context reduces interface drift.
