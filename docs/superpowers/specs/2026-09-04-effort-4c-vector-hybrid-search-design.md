# Effort 4C — sqlite-vec, Local Embeddings, Chunking, Hybrid Search

**Status:** approved
**Date:** 2026-09-04
**Bead:** Kraft-bj9.3 (child of Kraft-bj9)
**Component:** 3 — Indexer + Search Backend (`docs/consolidated/04_indexer_search.md` §7, §9, §11)
**Depends on:** Kraft-bj9.1 (4A), Kraft-bj9.2 (4B)

---

## 1. What this is

The last step of doc 04's build order: chunk indexed documents, embed the chunks
with a local model, store the vectors in `sqlite-vec` alongside FTS5, and answer
`mode=vector` and `mode=hybrid` on `/search` — which have returned 422 since 4A.

This closes Component 3.

## 2. Decisions, and what they close in §10

Doc 04 §10 lists three placeholders. This effort resolves all three, and says so
in the doc.

**Embedding stack: `fastembed` + `onnxruntime`, model `BAAI/bge-small-en-v1.5`,
384 dimensions.** Verified installing and importing on Python 3.14 in this
project (`fastembed` 0.8.0, `onnxruntime` 1.29.0, `sqlite-vec` 0.1.9). Chosen
over `sentence-transformers` because that pulls torch — roughly 2GB into a
project whose entire runtime dependency set is five small packages — for no
gain at this corpus size. §7 already notes the model is swappable without a
schema change, so this is a cheap decision to revisit.

**Chunking: heading-aware, then hard-wrapped.** Split on markdown ATX headings
first, since a spec's sections are its natural semantic units; any section longer
than the window is then split on paragraph boundaries into ~1200-character chunks
with 200 characters of overlap. Defaults live in one module-level constant each
so tuning is a one-line change, per §7's "tune once there's real corpus".

**Fusion: reciprocal rank fusion, k=60, equal weights.** `score = Σ 1/(k + rank)`
over each result list. RRF needs no score normalization between BM25 (unbounded,
negative) and cosine distance (0..2), which is exactly the problem that makes
weighted score blending fragile. §9 already called for RRF and called the weights
a placeholder; equal weights is the honest starting point.

**Embeddings are optional at runtime.** `fastembed` and its ~130MB model download
are a `vector` extra, not a hard dependency. If the extra is absent or the model
cannot load, FTS keeps working and `mode=vector` returns 422 with a message
naming the cause. Nobody who wants text search is forced into a model download.

**`mode` default becomes `hybrid`, degrading to `fts`.** Doc 04 §9 and doc 05
§4.3 both specify hybrid as the default. The response already echoes `mode`, so
it now reports the mode actually served — a request for `hybrid` on an instance
with no embedder is answered as `fts` rather than refused. An *explicit*
`mode=vector` still 422s there, because silently answering a vector query with
text ranking would be a lie about what was searched.

## 3. Schema

Both tables are additive; `INDEX_SCHEMA_VERSION` goes 1 → 2. The index is
disposable, so `open_index` may simply rebuild rather than migrate — but a
migration is one line here and preserves a populated FTS index, so add it.

```sql
CREATE TABLE document_chunks (
  id           TEXT PRIMARY KEY,
  document_id  TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index  INTEGER NOT NULL,
  chunk_text   TEXT NOT NULL,
  UNIQUE (document_id, chunk_index)
);
CREATE INDEX idx_document_chunks_document ON document_chunks(document_id);

CREATE VIRTUAL TABLE document_vectors USING vec0(
  chunk_id TEXT PRIMARY KEY,
  embedding float[384]
);
```

`vec0` cannot carry a foreign key, so `document_vectors` rows are deleted
explicitly whenever their chunks are — there is no cascade to lean on. Chunk
rewrites delete-then-insert both tables together, the same replace-whole shape
`replace_links` already uses for `document_links`.

## 4. Components

### 4.1 `kraft.index.chunk` (new)
Pure, no I/O, no model:

- `chunk_markdown(text: str, *, size: int = 1200, overlap: int = 200) -> list[str]`

Splits on ATX headings, keeps each heading with its body, then windows any
over-long section on paragraph boundaries with overlap. Empty or whitespace-only
input yields `[]`. A document shorter than `size` yields exactly one chunk.

### 4.2 `kraft.index.embed` (new)
The only module that knows about `fastembed`:

- `MODEL_NAME = "BAAI/bge-small-en-v1.5"`, `DIMENSIONS = 384`
- `Embedder.available() -> bool` — import succeeds
- `Embedder.encode(texts: list[str]) -> list[list[float]]` — lazy model load on
  first call, cached thereafter
- `Embedder.reason` — why it is unavailable, for `/health` and the 422 body

Model load and encoding run in `asyncio.to_thread`; neither blocks the loop.

### 4.3 `kraft.index.service.Indexer`
- Chunk + embed on upsert, only when a document's `content_hash` actually
  changed (§5 already computes this) — re-embedding unchanged text is the main
  avoidable cost here.
- `search(..., mode="hybrid")`: run FTS and vector, fuse by RRF, apply the same
  `source_kind` / `kind` / `repo` filters to both legs before fusion.
- `health()` gains an `embeddings` block: available, model name, chunk count,
  and the unavailability reason when absent.

### 4.4 `kraft.api`
`/search` accepts `mode` in `fts|vector|hybrid`, defaulting to `hybrid`. 422 only
for an unknown mode, or an explicit `mode=vector` with no embedder.

## 5. Error handling

| Case | Behavior |
|---|---|
| `fastembed` not installed | FTS works; `mode=vector` 422s naming the extra; `hybrid` serves `fts`. |
| Model download fails (offline, first run) | Same as above; the reason is surfaced in `/health`, not raised into ingestion. |
| Embedding raises mid-ingest | Log, keep the document and its FTS row, skip its vectors. Text search must never be lost to a model problem. |
| A document's chunks change | Delete its chunks and vectors, insert the new set, in one transaction. |
| Dimension mismatch after a model swap | `INDEX_SCHEMA_VERSION` bump rebuilds the index — that is what disposability is for. |

## 6. Testing

Hermetic, no model download in the default suite:

- `chunk_markdown`: heading splits, long-section windowing with overlap, short
  document yields one chunk, empty yields none, overlap actually overlaps.
- Reconcile writes chunks; a content change replaces them; a deleted document
  removes its chunks and vectors (no orphans left behind).
- RRF fusion is unit-tested against hand-built rank lists, including a document
  ranked by only one leg.
- `mode=vector` with no embedder 422s; `hybrid` degrades to `fts` and says so.
- Filters apply to the vector leg, not just the FTS leg.
- A real end-to-end embed+search test marked `slow`, skipped unless the model is
  already cached — CI must not download 130MB on every run.

## 7. Out of scope

- Re-ranking beyond RRF, and tuned fusion weights (§10 keeps that open until
  there is real usage).
- Embedding beads — they are not indexed at all (§8).
- UI mode radio; doc 05 §4.3 lists it, and it belongs with the 4C-UI slice.
