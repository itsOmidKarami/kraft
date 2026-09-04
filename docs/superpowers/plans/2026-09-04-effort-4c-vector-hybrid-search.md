# Effort 4C — Vector + Hybrid Search: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Chunk and embed indexed documents, store vectors in `sqlite-vec`, and serve `mode=vector` / `mode=hybrid` on `/search`.

**Architecture:** Two new pure-ish modules (`chunk`, `embed`) plus wiring in `Indexer`. Embeddings are an optional extra — every path degrades to FTS rather than failing.

**Tech Stack:** Python 3.14, sqlite-vec 0.1.9, fastembed 0.8.0 + onnxruntime 1.29.0, BAAI/bge-small-en-v1.5 (384-dim), FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-09-04-effort-4c-vector-hybrid-search-design.md`

## Global Constraints

- `sqlite-vec` is a hard dependency (small, pure loadable extension). `fastembed` goes in an optional `vector` extra — never import it at module top level in `service.py`.
- Text search must never break because embedding broke. Every embed failure logs and continues.
- Default suite must not download the 130MB model: the real embed test is marked `slow` and skips unless the model is already cached.
- `INDEX_SCHEMA_VERSION` 1 → 2 with an additive migration.
- Do not write `except (A, B):` where a single clause will do — and note ruff formats it per PEP 758 on py3.14.
- Gates: `uv run ruff check . && uv run ruff format .`, `uv run pytest -m "not e2e and not slow" -q`.

---

### Task 1: Dependencies and schema

**Files:** `pyproject.toml`, `src/kraft/index/db.py`, `tests/test_index_db.py`

**Interfaces produced:** `document_chunks` and `document_vectors` tables; `INDEX_SCHEMA_VERSION == 2`; `db.load_vec(conn)`.

- [ ] **Step 1:** Failing test — a fresh index has both tables, `vec_version()` answers, and `PRAGMA user_version` is 2. Plus: a v1 index migrates forward keeping its `documents` rows.
- [ ] **Step 2:** Run, watch it fail (`no such table: document_chunks`).
- [ ] **Step 3:** Add `sqlite-vec>=0.1.9` to `[project].dependencies` and a `[project.optional-dependencies] vector = ["fastembed>=0.8"]`. In `db.py`: load the extension in `connect()` (enable_load_extension, `sqlite_vec.load`, then disable again), add both tables to `INDEX_SCHEMA_SQL`, bump the version, add `_MIGRATIONS[1]` with the two `CREATE`s.
- [ ] **Step 4:** Run tests. **Step 5:** Commit.

**Note:** `vec0` virtual tables cannot be created unless the extension is loaded on that connection — `connect()` must load it before `_create_all` runs.

---

### Task 2: `kraft.index.chunk`

**Files:** create `src/kraft/index/chunk.py`, `tests/test_index_chunk.py`

**Interfaces produced:** `chunk_markdown(text, *, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP) -> list[str]`, `CHUNK_SIZE = 1200`, `CHUNK_OVERLAP = 200`.

- [ ] **Step 1:** Failing tests — empty input yields `[]`; a short document yields exactly one chunk equal to its text; a document with three `#` headings yields at least three chunks each containing its heading; a 5000-char section yields multiple chunks where consecutive chunks share `overlap` characters; every chunk is non-empty and no longer than `size` plus one paragraph's slack.
- [ ] **Step 2:** Run, watch fail. **Step 3:** Implement: split on `^#{1,6} ` (keeping the heading line with its body), then for any section longer than `size`, window it on `\n\n` boundaries with `overlap` characters carried forward. **Step 4:** Run. **Step 5:** Commit.

---

### Task 3: `kraft.index.embed`

**Files:** create `src/kraft/index/embed.py`, `tests/test_index_embed.py`

**Interfaces produced:** `MODEL_NAME`, `DIMENSIONS = 384`, `Embedder` with `available() -> bool`, `reason: str | None`, `encode(texts) -> list[list[float]]`, and `encode_async(texts)` wrapping it in `asyncio.to_thread`.

- [ ] **Step 1:** Failing tests — with `fastembed` importable but the model absent, `available()` is still True (availability is about the library, not a downloaded file); with the import patched to raise, `available()` is False and `reason` names the missing extra; `encode([])` returns `[]` without loading a model.
- [ ] **Step 2:** Run, watch fail. **Step 3:** Implement with a lazily-created `TextEmbedding` cached on the instance. **Step 4:** Run. **Step 5:** Commit.
- [ ] **Step 6:** Add a `@pytest.mark.slow` test that skips unless the model is already in the fastembed cache, asserting `len(encode(["hello"])[0]) == DIMENSIONS` and that two related sentences are closer than two unrelated ones.

---

### Task 4: Chunk and embed on ingest

**Files:** `src/kraft/index/ingest.py`, `src/kraft/index/service.py`, `tests/test_index_ingest.py`

**Interfaces produced:** `ingest.replace_chunks(conn, document_id, chunks, vectors)`; `Indexer` gains `_embedder`.

- [ ] **Step 1:** Failing tests — reconciling a document writes one `document_chunks` row per chunk; changing its content replaces them rather than appending; deleting the document leaves no chunks and no `document_vectors` rows; with no embedder, chunks are written and vectors are not.
- [ ] **Step 2:** Run, watch fail. **Step 3:** Implement `replace_chunks` (delete vectors by chunk id, delete chunks, insert both). Call it from `upsert_document` only when the content hash changed or the document is new. **Step 4:** Run. **Step 5:** Commit.

---

### Task 5: Vector and hybrid search

**Files:** `src/kraft/index/service.py`, `tests/test_index_service.py`

**Interfaces produced:** `Indexer.search(..., mode="hybrid")`; `rrf(*ranked_lists, k=60) -> dict[str, float]`; `Indexer.health()["embeddings"]`.

- [ ] **Step 1:** Failing tests — `rrf` unit cases including a document present in one list only; `mode="vector"` with no embedder raises a recognizable error; `mode="hybrid"` with no embedder returns FTS results; filters apply to the vector leg; `health()["embeddings"]` reports availability and chunk count.
- [ ] **Step 2:** Run, watch fail. **Step 3:** Implement. Vector leg: embed the query, `SELECT chunk_id, distance FROM document_vectors WHERE embedding MATCH ? ORDER BY distance LIMIT k`, map chunks to documents keeping each document's best chunk, then apply the same filters as FTS before fusing. **Step 4:** Run. **Step 5:** Commit.

---

### Task 6: API, docs, close out

**Files:** `src/kraft/api.py`, `tests/test_search_api.py`, `docs/consolidated/04_indexer_search.md`

- [ ] **Step 1:** Failing tests — `mode=hybrid` (and no `mode`) return 200 on an instance without embeddings, with `mode` echoing `fts`; `mode=vector` 422s naming the missing extra; `mode=nonsense` 422s.
- [ ] **Step 2:** Run, watch fail. **Step 3:** Implement in the `/search` handler. **Step 4:** Run.
- [ ] **Step 5:** Mark doc 04 §11 step 6 done, and replace §10's three placeholders with the decisions made (model, chunk defaults, RRF k) pointing at the design doc.
- [ ] **Step 6:** Full gates, `bd close Kraft-bj9.3`, commit, push, MR with auto-merge.

## Self-Review

**Spec coverage:** §3 schema→Task 1; §4.1→Task 2; §4.2→Task 3; §4.3 ingest→Task 4, search+health→Task 5; §4.4→Task 6; §5 error cases→Tasks 3–5; §6→every task; §2 doc updates→Task 6.

**Type consistency:** `DIMENSIONS = 384` is used by both the `vec0` declaration (Task 1) and `embed` (Task 3) — Task 1 hardcodes 384 in DDL, so a model change means a schema-version bump, which §5 already states.

**Caution:** `connect()` must load the vec extension before any `vec0` DDL runs, including inside `_create_all` on a fresh file.
