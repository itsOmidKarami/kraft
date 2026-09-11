# Indexer + Search Backend

> **Status:** historical design record. It captures why the system was designed
> this way, and is not maintained against the code. Current intended behaviour
> lives in `docs/intent/` (today: `gates` only).

> Component 3 of the decomposition (`01_conceptual_model.md` §12): the **Indexer +
> search backend** — local SQLite FTS5 (+ `sqlite-vec`) ingestion of `.engineering/`
> artifact files and session summaries, across every connected repo, powering
> cross-repo search. Refines `01` §6 (data model) and `02_orchestrator_core.md` §4/§5
> (state model, event bus).

## 0. Purpose

Full-text plus semantic search over spec/plan/review artifacts and session
summaries, across all ~10 repos, as one thing.

**Beads are not indexed.** Cross-repo bead queries run against the hydration hub
(`06_cross_repo_federation.md` §5), which answers `bd list` / `ready` / `dep` /
`search` cross-repo natively — structured queries cover the need, and `bd search`
covers full-text over bead bodies. Semantic search over beads is dropped; it returns
only as an ingestion source if that need ever materializes.

**Settled going in:** artifact `kind` is an open tag (derived from folder path or
front-matter), not a closed enum — every ingestion path is built to satisfy that from
the start, so a future `.engineering/`-folder convention slots in with zero schema
change. (The once-anticipated `.engineering/fix_notes/` kind turned out unnecessary —
fix tasks self-summarize as ordinary session summaries, §6.)

---

## 1. Where This Sits

The Indexer lives **inside** the Backend/Orchestrator process (`01` §2) — same
`asyncio` event loop, not a separate service. Two consequences: it subscribes to the
internal event bus directly (§2), and it can read `worker_sessions` for corroboration
(§6) rather than only inferring linkage from git-native files.

Index DB is a **second SQLite file**, separate from the orchestrator's state DB
(`work_items` / `events` / `worker_sessions`) — different lifecycle: disposable,
rebuildable by re-scan (`01` §6), vs. authoritative and never rebuilt from source.

---

## 2. Indexing Triggers

**No recurring poll.** Within this component's ingestion scope, changes are almost
entirely orchestrator-caused — the orchestrator knows the instant they happen.
Principle 7 ("polling for what we don't control, live for what we do") argues
*against* a generic poll loop here.

- **Event-driven (primary).** The Indexer subscribes to the internal event bus
  (`02` §5) the way a WS client does. On `worker_session_completed` for an
  artifact-producing hook (spec / plan / chain-review / session-summary), it enqueues
  a targeted re-index of just that path — no repo-wide scan.
- **Startup full scan.** Once, when the orchestrator process starts, before resuming
  normal event handling — catches drift from while the process was down (`git pull`,
  offline hand-edits). Bounded fan-out across repos (small `asyncio.gather` cap), not
  fully sequential.
- **Piggyback on `on.env.prepare`.** The orchestrator is already touching that
  worktree fresh at that moment, so a repo-scoped rescan rides along at no extra
  mechanism cost.
- **Manual escape hatch.** `POST /index/rescan?repo=<repo>` for the rare case someone
  hand-edits a file mid-session and doesn't want to wait. On-demand only, no scheduled
  loop.

The Work-graph writes (`on.intake`, `on.completed`) do **not** trigger a `documents`
re-index — nothing bead-shaped is indexed. Those events instead trigger a hub
`bd repo sync` for that repo (`06_cross_repo_federation.md` §5.3).

CI/review-comment polling (`on.ci.poll`, `on.review.mr.run`) is a separate mechanism
(GitLab plugin, HttpClientAdapter) — not in this component's ingestion scope.

---

## 3. Schema

```
documents
  id               TEXT PRIMARY KEY   -- uuid assigned at first sighting
  repo             TEXT
  source_kind      TEXT               -- closed enum: 'artifact' | 'session_summary'
  kind             TEXT NULL          -- OPEN TAG. folder-derived (segment after
                                         .engineering/) unless front-matter `kind:`
                                         overrides.
  title            TEXT
  path             TEXT
  content          TEXT               -- markdown body as-is
  content_hash     TEXT
  metadata_json    TEXT               -- front-matter extras; absorbs new kinds/fields
                                         with no migration
  source_created_at, source_updated_at
  indexed_at

documents_fts      -- FTS5 virtual table, external-content mode over
                      documents(title, content), content_rowid keyed to documents.id.
                      Filterable columns (source_kind, kind, repo) stay on the real
                      table.

document_chunks
  id               TEXT PRIMARY KEY
  document_id      TEXT               -- FK documents.id
  chunk_index      INTEGER
  chunk_text       TEXT

document_vectors   -- sqlite-vec virtual table, keyed to document_chunks.id (§7)

document_links
  id               TEXT PRIMARY KEY
  document_id      TEXT               -- FK documents.id
  work_item_id     TEXT NULL          -- bead ref; many rows per document (M:N).
                                         resolves against the hydration hub, not
                                         against a documents row
  node_id          TEXT NULL
  hook_point       TEXT NULL
  worker_session_id TEXT NULL
```

- `kind` is the only place the open-tag constraint lives — a plain nullable column,
  no enum, no lookup table.
- `source_kind` is the one closed enum, and it reflects the genuinely different
  *ingestion mechanisms* (git file, session artifact) — not content taxonomy, so it
  doesn't grow. It is **two values**: bead ingestion was removed.
- `document_links.work_item_id` **stays** — still a bead ref, still the M:N link from
  an artifact or session summary to the beads it concerns. Those ids resolve against
  the hub. Bead status / priority / deps / `chain_template` live on the bead itself,
  never copied here.
- All `documents.id` are uuid-at-first-sighting. No bead-id special case.

---

## 4. Ingestion Mechanics Per `source_kind`

**`artifact`** — `git ls-files` under `.engineering/**/*.md` per repo. `kind` = folder
segment right after `.engineering/`, overridable by a `kind:` front-matter key. `id`
assigned at first sighting, stable across renames (§5). Front-matter parsed into
`metadata_json`; body into `content`.

**`session_summary`** — mechanically identical to `artifact` ingestion (git file
under `.engineering/`), see §6 for location and linkage front-matter.

There is no `bead` ingestion path — `bd list --json` is not called by the Indexer.

---

## 5. Renamed / Deleted Content

Per-repo, per-`source_kind` diff on every trigger: current path/id list from source
vs. `documents` rows for that repo/kind.

- **New, unseen `content_hash`** → insert.
- **Known path missing, no hash match elsewhere** → delete the row (and its
  FTS/chunk/vector rows). No tombstoning — the index is disposable and rebuildable, a
  wrong delete costs nothing.
- **Missing path, but its `content_hash` matches a path that just appeared** →
  rename. Update `path` in place, keep `id` stable. Matters because `document_links`
  and `document_vectors` point at `id`.
- **Same path, changed `content_hash`** → re-extract; re-chunk/re-embed only if the
  hash actually changed.

---

## 6. Session Summaries: Location & Node/Task Linkage

`01` §5/§6 name `.engineering/sessions/` in the artifact-store list, and `01` §6
treats summaries as git-tracked, path-referenced artifacts with a pointer, consistent
with `03` §3's `session_summary_ref` result-file field being parsed exactly like
`spec_artifact_ref` / `plan_artifact_ref`. This section pins down the ingestion
detail: summaries are handled through the same open-tag mechanism as spec/plan/review,
not as a special case.

**Location and tag:** summaries live at `.engineering/sessions/`,
`kind='session_summary'` — another open-tag folder. Front-matter carries linkage:

```yaml
work_item_ids: [...]        # many-to-many, per 01 §6
node_id: verify
hook_point: on.test.run
worker_session_id: <uuid>
```

Ingestion writes one `document_links` row per `work_item_id`, plus a row carrying
`node_id` / `hook_point` / `worker_session_id` — giving "everything from the `verify`
node of work item X" for free.

A fix task self-summarizes like any `HeadlessAgentInvocation` session:
`kind='session_summary'`, front-matter `node_id` = `verify` / `mr_checks`,
`hook_point` = `on.implementation.start`. No separate `.engineering/fix_notes/` kind
is needed — zero schema change (`chain_backward_motion_addendum_v1.md` §7).

Because the Indexer is co-located with the orchestrator (§1), it can cross-check this
front-matter against `worker_sessions` (which has `node_id` and `session_summary_ref`)
rather than trusting the file alone — front-matter keeps the linkage
git-native/portable, the DB join is free corroboration when available.

**Implementation-time check — settled (Kraft-bj9.7).** `03_plugin_adapters.md` §2's
result-file schema declares `session_summary_ref: string` but didn't say where the
referenced file lives. The built `HeadlessAgentInvocation` (`kraft.adapters.agent`)
injects the work-item / node / hook-point / session id into the system prompt and
instructs the worker to write `.engineering/sessions/<worker_session_id>.md` with the
front-matter above, then report that repo-relative path as `session_summary_ref`. The
adapter parses it into `worker_sessions.session_summary_ref` on session exit (and on
resolve-from-file after a reattach), so the DB corroboration join in this section is
available. Ingestion (4B) can rely on both the path convention and the column.

---

## 7. Vector / Semantic Search

- **`sqlite-vec`**, not a separate engine. `02` §2 commits to SQLite; `01` §6 calls
  the whole index disposable/rebuildable. A second storage engine (LanceDB, Chroma, a
  standalone Faiss file) breaks both — `sqlite-vec` keeps vectors in the same file as
  FTS5, one rebuild path.
- **Embedding source: local model**, confirmed. Fully offline, fits the macOS-first /
  no-network-dependency posture. Exact model — a small, fast local model
  (sentence-transformers/ONNX class) — is a placeholder, swappable later without a
  schema change since embeddings live in their own table keyed to `document_chunks`.
- **Chunking.** Specs/plans run long; whole-document embedding loses granularity.
  Simple heading/paragraph-based chunking to start; size/overlap are placeholders,
  tune once there's real corpus.

---

## 8. Beads Boundary

**Resolved: beads are not indexed** (`06_cross_repo_federation.md` §4). The original
sole reason to index them — `bd` is per-repo and cross-repo bead querying didn't
exist — is answered by the hydration hub. Bead ingestion, the `bead` `source_kind`,
and `bd list --json` are all gone from the Indexer.

Two rules carry through:

- **Write direction.** The Indexer never writes back to `bd`. `document_links` is the
  complete, authoritative many-to-many graph, built purely by scanning git-native
  files (front-matter) plus reading bd's existing structured fields.
- **Read direction (hard rule, generalized).** *Nothing that needs current state
  reads it from a derived/lagging store.* This applied to the old bead shadow in the
  index; it now applies to the hydration hub too — the hub lags one export+sync
  cycle, so gating decisions (policy engine, node transitions, "is this bead still
  open" for a gate) read the owning repo's `bd` directly, never the hub. The hub, and
  the index, are for discovery and cross-repo graph queries — not authority.

---

## 9. Search Surface

New local API endpoints, same tier as `02` §10's `/work-items` surface.

```
GET /search?q=<query>&source_kind=&kind=&repo=&mode=fts|vector|hybrid
```

- `fts`: FTS5 BM25 rank only.
- `vector`: cosine similarity over `document_chunks` / `document_vectors` only.
- `hybrid` (default): both, merged via reciprocal-rank fusion — simple, no tuning to
  start; fusion weights are a placeholder.
- Response includes resolved `document_links` inline (work items, node/hook-point
  where applicable) so the UI doesn't need a second round-trip per result.
- The `source_kind` filter has **no `bead` value** — `/search` returns artifacts and
  session summaries only.

```
GET /documents/{id}
```

Returns a single `documents` row in full — `content` (actual markdown, not a
snippet), `metadata_json`, `kind`, `source_kind`, `repo`, `path`, plus resolved
`document_links` for that document. No bead documents exist, so this never returns
one.

```
GET /work-items/{id}/documents
```

Returns `document_links` rows scoped to `work_item_id = {id}`, joined with each
linked document's `title` / `kind` / `source_kind` / `path` (not full `content` — the
UI fetches that per-document via `GET /documents/{id}` when someone opens one). Chosen
over a `work_item_id` filter on `/search` because this isn't a text-search operation —
it's "list what's linked to this item," a different access pattern (no query string,
no ranking).

```
POST /index/rescan?repo=<repo>
```

The manual escape hatch from §2.

Bead free-text search is a **separate sibling endpoint**, `GET /beads/search?q=`, a
thin passthrough to `bd search --json` against the hydration hub (returns bead id,
title, repo, status, snippet). Not part of this component's build — owned by
component 5 (`06_cross_repo_federation.md` §6.1), listed on the orchestrator API
surface (`02` §10.1).

---

## 10. Open Questions / Placeholders

All three original placeholders were settled by Effort 4C
(`docs/superpowers/specs/2026-09-04-effort-4c-vector-hybrid-search-design.md`);
each remains cheap to revisit, since embeddings live in their own table and a
model change is an index rebuild, not a migration.

- ~~Exact local embedding model choice.~~ **`BAAI/bge-small-en-v1.5`** (384-dim)
  via `fastembed` + `onnxruntime`, chosen over `sentence-transformers` to avoid a
  torch dependency. Ships as the optional `vector` extra: without it FTS still
  works, `hybrid` degrades to `fts`, and an explicit `mode=vector` is refused.
- ~~Chunking size/overlap defaults.~~ **Heading-aware, then 1200 characters with
  200 of overlap** (`kraft.index.chunk.CHUNK_SIZE` / `CHUNK_OVERLAP`).
- ~~Hybrid-search fusion weights.~~ **Reciprocal rank fusion, k=60, equal
  weights** (`kraft.index.service.RRF_K`). RRF needs no score normalisation
  between BM25 and cosine distance, which is what made weighted blending
  fragile. Still worth tuning once there is real usage.

---

## 11. Build Order

1. ✅ **Schema + migrations** (Effort 4A) — `documents`, `documents_fts`,
   `document_links` (links table created, populated in 4B). Second SQLite file,
   disposable/rebuildable.
2. ✅ **Artifact ingestion** (Effort 4A) — git scan, front-matter parse,
   content-hash diff (§4, §5). Exercises the open-tag `kind` mechanism end to end.
3. ✅ **Event-bus subscription + startup scan** (Effort 4A) — `on_commit`
   multiplexer, `work_item_completed` triggers a targeted repo rescan, one startup
   full scan, `POST /index/rescan` escape hatch. No poll loop (§2). Connected-repo
   set = distinct `work_items.repo` ∪ `KRAFT_INDEX_REPOS`.
4. ✅ **Session-summary ingestion + node/task linkage** (Effort 4B, bead Kraft-bj9.2) —
   event-driven off `worker_sessions.session_summary_ref` rather than by git scan,
   because a live summary sits untracked in the work item's worktree; the scan
   classifies `.engineering/sessions/` as `session_summary` so merged summaries
   update the same `(repo, path)` row. Summary reconcile is upsert-only. Also lands
   `GET /work-items/{id}/documents`, links resolved inline in `/search` and
   `/documents/{id}`, and the `on.env.prepare` piggyback rescan. See
   `docs/superpowers/specs/2026-09-04-effort-4b-session-summaries-design.md`.
5. ✅ **FTS5 `/search` endpoint, text-only** (Effort 4A) — plus `GET /documents/{id}`.
   `mode=fts` only; `mode=vector|hybrid` rejected with 422 until 4C.
6. ✅ **`sqlite-vec` + local embedding + chunking + hybrid ranking** (Effort 4C,
   bead Kraft-bj9.3) — the optional half from `01` §11. Vectors live in the same
   SQLite file as FTS5 (`document_chunks` + a `vec0` `document_vectors` table),
   embeddings come from a local ONNX model behind the optional `vector` extra,
   and `/search` fuses the two legs with RRF. `mode` now defaults to `hybrid`
   and the response echoes the mode actually served, so an instance without the
   extra answers with `fts` rather than failing.

**Component 3 is complete.**

---

## Changelog

Consolidated from `indexer_search_backend_design_v1.md`, with
`cross_repo_federation_design_v1.md` §4 fully applied, §§4–5 of
`orchestrator_indexer_api_addendum_v1.md` (the `/documents/{id}` and
`/work-items/{id}/documents` endpoints) folded in, and the fix-task self-summary
resolution from `chain_backward_motion_addendum_v1.md` §7.

- **Bead ingestion removed throughout** (`cross_repo_federation_design_v1.md` §4):
  `source_kind` enum shrank `'bead' | 'artifact' | 'session_summary'` →
  `'artifact' | 'session_summary'`; the `bead` ingestion subsection (old §4) deleted;
  `bd list --json` gone from the Indexer; old §2's `on.intake`/`on.completed` →
  `documents` re-index trigger replaced by a hub `bd repo sync`; `/search` loses its
  `bead` filter value; old build-order step 3 ("Bead ingestion") deleted and steps
  renumbered.
- **§8** — the original's long "worth revisiting whether bead ingestion belongs here
  at all" deliberation is now a short "resolved: removed." The read-direction hard
  rule ("nothing that needs current state reads it from the Index") is retained and
  generalized to cover the hydration hub.
- **§9** — `GET /documents/{id}` and `GET /work-items/{id}/documents` added from
  `orchestrator_indexer_api_addendum_v1.md` §§4–5. `GET /beads/search` noted as the
  sibling endpoint that replaces the bead half of `/search`.
- **§3** — the original schema block already carried the amended (2-value) enum with
  a "reflects the component-5 change" note; that note is now just the schema.
- **§6** — fix tasks self-summarize as `kind='session_summary'` with
  `node_id`=`verify`/`mr_checks`; the long-anticipated `.engineering/fix_notes/` kind
  is confirmed unnecessary (`chain_backward_motion_addendum_v1.md` §7). The original's
  "gap the source docs leave open" framing for the `sessions/` folder is dropped —
  `01` §5/§6 now enumerate it.
