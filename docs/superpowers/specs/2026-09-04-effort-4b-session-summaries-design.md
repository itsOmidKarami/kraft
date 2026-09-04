# Effort 4B — Session-Summary Ingestion, Document Links, Work-Item Documents

**Status:** approved
**Date:** 2026-09-04
**Bead:** Kraft-bj9.2 (child of Kraft-bj9)
**Component:** 3 — Indexer + Search Backend (`docs/consolidated/04_indexer_search.md`)
**Depends on:** Kraft-bj9.1 (Effort 4A), Kraft-bj9.7 (worker session summaries)

---

## 1. What this is

Build-order step 4 of doc 04. Effort 4A indexed `.engineering/` artifacts and
shipped FTS `/search`. Kraft-bj9.7 made the Execution Worker write
`.engineering/sessions/<worker_session_id>.md` and report the path as
`session_summary_ref`. This effort ingests those summaries, populates
`document_links`, resolves links into every read path, and adds
`GET /work-items/{id}/documents`.

Backend only. UI consumption (doc 05's linked-documents panel, link chips in the
search overlay) is a separate 4B-UI bead, mirroring the 4A / 4A-UI split.

**No index schema change.** `document_links` and its two indexes were created in
4A and left empty on purpose (`src/kraft/index/db.py`). This effort fills them.

## 2. The discovery problem, and the decision

A session summary is written by the agent into the **worktree**
(`<run>/worktrees/<work_item_id>`, branch `kraft/<work_item_id>` —
`src/kraft/builtins.py`), where it is **untracked**. Artifact ingestion runs
`git ls-files -- .engineering/` against `work_items.repo`, the source checkout
(`src/kraft/index/ingest.py`). Two gaps follow: the file is not tracked, and it
is not on the branch the scanned checkout has out. A summary would therefore stay
invisible for the entire life of the work item — exactly the window in which the
UI wants it.

**Decision: ingest event-driven from the ref, and let the scan pick up merged
summaries later.**

- The orchestrator already holds the authoritative pointer:
  `worker_sessions.session_summary_ref`, written on session exit. When a session
  exits carrying a ref, the Indexer reads that one file and upserts it. No git
  scan, no tracked/untracked question.
- The git scan additionally classifies anything under `.engineering/sessions/`
  as `source_kind='session_summary'` rather than as an artifact. So once a work
  item merges and the summary lands in the source checkout, the scan produces a
  row of the **same shape at the same `(repo, path)`** — an update, not a
  duplicate — and a rebuilt index still carries every historical summary.

This deviates from doc 04 §4's "mechanically identical to artifact ingestion".
The deviation is deliberate and narrow: the *extraction* is identical (same
front-matter parse, same title/kind derivation, same content hash); only the
*trigger and file source* differ. Doc 04 §2 already frames the Indexer as
event-driven off the internal bus, so this is the same stance applied one level
finer.

## 3. Deletion

`documents.repo` / `path` stay keyed to the source repo, so a live worktree
summary looks "missing" to every scan of that repo. §5's delete rule would drop
it on the next trigger.

**Session-summary reconcile is upsert-only — it never deletes.** A summary whose
file is gone leaves a stale row until the next full index rebuild. That is cheap:
the index is disposable (§1/§5), summaries are append-only by nature, and a wrong
delete here costs a live document. Artifact reconcile keeps its existing
insert/update/rename/delete diff unchanged.

## 4. Linkage: who wins

Front-matter (04 §6) and `worker_sessions` can disagree; an agent may omit
front-matter entirely.

| Field | Source | Why |
|---|---|---|
| `node_id`, `hook_point`, `worker_session_id` | `worker_sessions` row | The orchestrator wrote these into the prompt; the agent only echoed them back. The DB is the original. |
| `work_item_ids` | front-matter, **unioned** with the session's own `work_item_id` | 01 §6 makes this M:N — a summary may legitimately name items beyond the one it ran under. The union guarantees at least the owning item is linked. |

A disagreement on a DB-owned field logs a warning and does not block ingestion.
Missing or malformed front-matter still ingests, linked from the DB alone.

Rows written per summary document, per 04 §6:

- one row per `work_item_id` (`work_item_id` set, session fields NULL)
- one row carrying `node_id` / `hook_point` / `worker_session_id`
  (`work_item_id` NULL)

Link writes are **replace-all per document**: delete this document's rows, insert
the current set. Idempotent under re-ingestion from either path.

Scan-discovered summaries (merged, no live session context) link from
front-matter alone; where front-matter names a `worker_session_id` that exists in
`worker_sessions`, the DB values for `node_id` / `hook_point` override.

## 5. Components

### 5.1 `kraft.index.ingest`

- `ScannedDoc` gains `source_kind: str` and `links: list[LinkRow]`.
- `source_kind_for(path)` — `session_summary` when the path is under
  `.engineering/sessions/`, else `artifact`. One function, used by both the scan
  and the event path.
- `links_from_front_matter(fm)` — parses `work_item_ids` (list or scalar),
  `node_id`, `hook_point`, `worker_session_id`; ignores malformed values.
- `scan_repo` sets `source_kind` per file and parses links for summaries.
- `reconcile(conn, repo, scanned)` splits by `source_kind`: the artifact bucket
  keeps today's full diff; the summary bucket upserts only. It writes
  `document_links` for every document it inserts or updates.
- `upsert_document(conn, repo, doc)` — the shared insert-or-update used by both
  buckets and by the event path, keyed on `(repo, path)`.

### 5.2 `kraft.index.service.Indexer`

- Constructor takes `run_dirs` (optional) so it can resolve a worktree path;
  without it, the event path falls back to the source repo and logs.
- `ingest_session_summary(session_id) -> bool` — loads the `worker_sessions` row
  and its work item, resolves the file at
  `<run>/worktrees/<work_item_id>/<ref>` (falling back to `<repo>/<ref>`),
  extracts it exactly as the scan would, upserts, writes links per §4. Returns
  False and logs when the file is absent — a worker that reported a ref it never
  wrote must not break the drain.
- Event drain gains two triggers:
  - `worker_session_exited` → if that session has a `session_summary_ref`,
    ingest it.
  - `worker_session_started` with `hook_point == 'on.env.prepare'` → targeted
    rescan of that work item's repo. This is doc 04 §2's `on.env.prepare`
    piggyback: the moment a work item starts touching a repo is a good moment to
    refresh that repo's artifacts.
- `search()` and `get_document()` resolve `links` instead of returning `[]`.
  Search resolves in one batched query over the result set, not per row.
- `documents_for_work_item(work_item_id)` — `document_links` rows for that work
  item joined to `title` / `kind` / `source_kind` / `path` / `repo`. No
  `content`; the UI fetches that per document.

### 5.3 `kraft.api`

`GET /work-items/{id}/documents` → `{"work_item_id": ..., "documents": [...]}`.
404 when the work item is unknown, `[]` when it has no linked documents. Reuses
the existing work-item lookup so an unknown id is refused the same way the rest
of the surface refuses it.

## 6. Data flow

```
agent writes .engineering/sessions/<sid>.md in the worktree
  → result file carries session_summary_ref
    → subprocess adapter stores it on worker_sessions, emits worker_session_exited
      → Indexer drain sees the event, reads the file from the worktree
        → upsert documents row (repo = source repo, path = ref)
        → replace document_links rows (work items ∪ session, node/hook/session)

later, on merge: summary becomes tracked in the source repo
  → next scan classifies it as session_summary at the same (repo, path)
    → update in place, links refreshed from front-matter (+ DB where resolvable)
```

## 7. Error handling

| Case | Behavior |
|---|---|
| Ref points at a file that does not exist | Log, skip, return False. No event, no partial row. |
| Ref escapes the worktree (`../`, absolute) | Reject before reading. A worker-supplied path is untrusted input. |
| Front-matter malformed or absent | Ingest anyway; link from the DB alone. |
| Front-matter disagrees with the DB on a DB-owned field | Warn, use the DB value. |
| Work item or session row missing | Skip with a warning — the index never blocks the executor. |
| Any exception inside the drain | Already caught per-iteration in `_run`; keep that boundary. |

## 8. Testing

Hermetic, no real agent:

- `source_kind_for` and `links_from_front_matter` unit cases, including
  malformed front-matter and a scalar `work_item_ids`.
- Scan of a repo containing both an artifact and a tracked summary → two rows,
  correct `source_kind`, links written for the summary only.
- Upsert-only: a scan that no longer lists a previously ingested summary leaves
  the row; the same scan still deletes a missing artifact.
- Event path: seed a work item + session with a ref, write the file into the
  worktree, notify the drain → document + links exist, `search` returns it with
  links resolved.
- Precedence: front-matter naming a different `node_id` than the DB → DB wins,
  extra `work_item_ids` are kept.
- Path traversal: a ref of `../../etc/passwd` is refused.
- Idempotence: ingesting twice leaves exactly one document and one set of links.
- `GET /work-items/{id}/documents`: 404 unknown, `[]` when unlinked, joined
  fields when linked.
- The existing `test_e2e_happy_path` gains an assertion that the completed work
  item has its session summary linked — the fixtures already write one
  (Kraft-bj9.7).

## 9. Out of scope

- UI consumption (4B-UI bead).
- `sqlite-vec`, chunking, embeddings, `mode=vector|hybrid` (4C, Kraft-bj9.3).
- Bead ingestion — does not exist and will not (04 §8).
- Committing summaries into git from the chain. Merging is the chain's job
  elsewhere; this effort does not add a commit step.

## 10. Open questions

None blocking. One to revisit in 4C: chunking a session summary is likely
lower-value than chunking a spec, so `document_chunks` population may want a
per-`source_kind` policy rather than treating both alike.
