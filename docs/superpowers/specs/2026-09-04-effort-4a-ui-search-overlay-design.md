# Effort 4A-UI — search overlay + document viewer

**Status:** design agreed
**Date:** 2026-09-04
**Epic:** Kraft-bj9 · **Bead:** Kraft-bj9.5
**Design source:** `docs/consolidated/05_ui.md` §4.3 (Search), §4.4 (Document Viewer), build-order step 7
**Consumes:** the 4A backend — `GET /search`, `GET /documents/{id}` (`docs/consolidated/04` §9, shipped in `docs/superpowers/specs/2026-09-04-effort-4a-indexer-fts-search-design.md`)

---

## 0. Why now

Effort 4A shipped `GET /search` and `GET /documents/{id}` this session. This
is the SPA slice that consumes them — the natural continuation of both
Effort 3B (the SPA) and 4A (the search backend), and doc 05 build-order
step 7.

It is frontend-only. It follows the existing SPA idiom exactly (plain CSS,
local component state, `api.ts` wrappers, modal-on-backdrop like `LogModal`
/ `IntakeModal`) — doc 05 §1 rules out a component library and a design
system, so consistency with 3B is the whole aesthetic brief.

---

## 1. Scope

### In (doc 05 §4.3 / §4.4, trimmed to what 4A's backend supports)

- **Global search overlay** — reachable from every view via a header
  affordance and a keyboard shortcut. Modal over a backdrop, not a route.
  - Debounced query box → `GET /search?q=` (`mode=fts`, the only mode 4A
    serves).
  - **Advanced filters** (collapsed by default): `source_kind`
    (`artifact` / `session_summary`), `kind` (free text), `repo` (select,
    options from the work-item store — same source the Board toolbar uses).
  - **Results list**: title, `kind` badge, `repo`, snippet (rendered with
    the `[` / `]` match markers `/search` returns turned into `<mark>`).
  - Empty query → no request, empty state. No results → "no matches".
    Request error → inline message (the 422 cases from 4A: bad FTS syntax,
    etc.).
- **Document viewer** — opened by clicking a result.
  - `GET /documents/{id}` → title, metadata panel (`kind`, `source_kind`,
    `repo`, `path`, `source_created_at`, `source_updated_at`), and the
    document body.
  - Body render: raw markdown in a `<pre class="doc-body">` with
    `white-space: pre-wrap`. **Deliberate simplification** — spec/plan
    artifacts are already human-readable as source; a markdown renderer is
    a new dependency for marginal gain. `ponytail:` add `react-markdown`
    if plain-text reading proves painful.
  - Back to the results list without re-querying.

### Out

| Deferred item | Why | Tracked |
|---|---|---|
| `mode=vector` / `mode=hybrid` radio | 4A backend rejects them with 422 | Effort 4C (Kraft-bj9.3) |
| `document_links` breadcrumb to work items | 4A `/search` + `/documents/{id}` return `links: []` | Effort 4B (Kraft-bj9.2) |
| "Linked documents" panel on Work Item Detail (`GET /work-items/{id}/documents`) | endpoint is 4B | Effort 4B (Kraft-bj9.2) |
| Bead-scoped search box (`GET /beads/search`) | component 5, endpoint does not exist | `06` §6.1 |
| Markdown rendering | see simplification above | new bead if needed |
| Playwright e2e for search | needs an index-seeded fixture repo + `KRAFT_INDEX_REPOS` in `e2e/serve.py`; the existing e2e is `manual` / `allow_failure` anyway | new bead (below) |

---

## 2. Decisions (flagged for review)

1. **Overlay, not a route.** Matches `LogModal` / `IntakeModal`. Deep-linking
   a search or a document is not a v1 need; a route adds history/back-button
   handling for no benefit at solo scale.
2. **No store slice.** Search state (query, filters, results, open document)
   is local to the `SearchOverlay` component. Doc 05 §2's normalized store is
   for live work-item/event state; §4.3's hard rule is that search results
   are a *lagging shadow* never treated as current — keeping them out of the
   store enforces that structurally. `HealthBadge` and `LogModal` already
   fetch their own data this way.
3. **Keyboard shortcut = `Cmd/Ctrl-K`** to open, `Esc` to close. `/` is *not*
   bound — it collides with typing `/` into the Board's repo-path fields.
4. **Repo filter options** come from `Object.values(useStore.getState().workItems)`
   — the same derivation the Board toolbar uses. A repo with indexed docs but
   no work item won't appear; acceptable (the `repo` field also accepts being
   left blank = all repos, which is the common case).
5. **Debounce = 250 ms**, min query length 1. The request is cheap (localhost
   SQLite FTS) so no need to gate harder.
6. **Snippet rendering:** `/search` returns snippets with literal `[` … `]`
   around matches (4A's `snippet()` config). Split on those and wrap the
   inner spans in `<mark>`; everything else is plain text (no `dangerouslySetInnerHTML`).

---

## 3. Files

| File | Change |
|---|---|
| `frontend/src/types.ts` | +`SearchResult`, `SearchResponse`, `DocumentDetail` |
| `frontend/src/api.ts` | +`search(params)`, +`getDocument(id)` |
| `frontend/src/components/SearchOverlay.tsx` | new — the overlay (query + filters + results + hosts the viewer) |
| `frontend/src/components/DocumentModal.tsx` | new — single-document view |
| `frontend/src/components/Snippet.tsx` | new — tiny `[..]` → `<mark>` renderer (its own file so it is unit-testable) |
| `frontend/src/App.tsx` | header "Search" button + global `keydown` listener + overlay mount |
| `frontend/src/styles.css` | `.search-overlay`, `.search-result`, `.doc-body`, `.doc-meta`, `mark` |
| `frontend/src/components/SearchOverlay.test.tsx` | new |
| `frontend/src/components/DocumentModal.test.tsx` | new |
| `frontend/src/components/Snippet.test.tsx` | new |
| `frontend/src/App.test.tsx` | extend — shortcut opens the overlay |

No new dependencies.

---

## 4. Interfaces

### `types.ts`

```ts
export interface SearchResult {
  id: string;
  repo: string;
  source_kind: "artifact" | "session_summary";
  kind: string | null;
  title: string;
  path: string;
  snippet: string;
  score: number;
  links: unknown[]; // always [] in 4A
}

export interface SearchResponse {
  query: string;
  mode: string;
  results: SearchResult[];
}

export interface DocumentDetail {
  id: string;
  repo: string;
  source_kind: "artifact" | "session_summary";
  kind: string | null;
  title: string;
  path: string;
  content: string;
  metadata: Record<string, unknown>;
  source_created_at: string | null;
  source_updated_at: string | null;
  indexed_at: string;
  links: unknown[];
}
```

### `api.ts`

```ts
export const search = (params: {
  q: string;
  source_kind?: string;
  kind?: string;
  repo?: string;
}) => {
  const qs = new URLSearchParams({ q: params.q });
  if (params.source_kind) qs.set("source_kind", params.source_kind);
  if (params.kind) qs.set("kind", params.kind);
  if (params.repo) qs.set("repo", params.repo);
  return req<SearchResponse>(`/search?${qs}`);
};

export const getDocument = (id: string) =>
  req<DocumentDetail>(`/documents/${encodeURIComponent(id)}`);
```

`req` already sends `Accept: application/json` and throws `new Error(detail)`
on non-2xx — the 422 bodies from 4A (`{"detail": "bad search query: …"}`)
surface as the thrown message.

### `SearchOverlay.tsx`

```
<SearchOverlay onClose={() => void} />
```

State: `q`, `sourceKind`, `kind`, `repo`, `advanced` (bool), `results`,
`error`, `loading`, `openDoc` (id | null). Debounced effect on
`[q, sourceKind, kind, repo]` calls `api.search`; empty `q` clears results
and skips the call. Renders `<DocumentModal>` when `openDoc` is set.

### `DocumentModal.tsx`

```
<DocumentModal id={string} onClose={() => void} />
```

Fetches on mount / `id` change (`live` guard like `LogModal`). Shows
`loading…` / error / the document.

### `Snippet.tsx`

```
<Snippet text={string} />   // "[foo] bar [baz]" -> <mark>foo</mark> bar <mark>baz</mark>
```

---

## 5. Behaviour details

- **Open:** header `Search` button, or `Cmd-K` / `Ctrl-K` anywhere. The
  global listener lives in `App`; it ignores the combo when the overlay is
  already open (the overlay's own input keeps focus).
- **Close:** `Esc`, backdrop click, or an explicit `close` button. Closing
  the document viewer returns to the results (does not close the overlay).
- **Focus:** the query input autofocuses on open.
- **A11y:** overlay is `role="dialog" aria-modal="true" aria-label="Search"`;
  document modal `role="dialog" aria-label="document"`. Results are a `<ul>`
  of `<button>`s (keyboard-activatable). Consistent with `IntakeModal`.
- **The §4.3 hard rule:** nothing here links straight into live work-item
  state in 4A (no `links`). When 4B adds links, the click-through to Work
  Item Detail must go through the normal route so `hydrateItem` re-fetches —
  noted for 4B, nothing to build now.

---

## 6. Testing

Vitest + Testing Library, matching `IntakeModal.test.tsx` (mock `api.*` with
`vi.spyOn`, render under `MemoryRouter` where a component uses router hooks —
`SearchOverlay` does not, but `App` does).

| Test | Asserts |
|---|---|
| `Snippet.test.tsx` | `[x] y [z]` → two `<mark>` with `x` / `z`, `y` plain; no markers → plain text; unbalanced `[` → treated as literal text, no crash |
| `SearchOverlay.test.tsx` | typing a query calls `api.search` once after debounce with the query; results render title + repo + snippet marks; `source_kind` select adds the param; a thrown error renders inline; empty query makes no call; clicking a result fetches + shows the document; closing the doc returns to results |
| `DocumentModal.test.tsx` | renders `content` in a `<pre>`, metadata panel shows `kind`/`repo`/`path`; error path shows the message |
| `App.test.tsx` (extend) | `Ctrl-K` opens the overlay (`role="dialog"` name "Search" appears); `Esc` closes it |

`npm run build` (tsc + vite) and `npm test` green. Python untouched.

New bead to file: **search UI Playwright e2e** — seed `e2e/serve.py`'s
fixture repo with `.engineering/*.md` and set `KRAFT_INDEX_REPOS`, then a
spec that opens the overlay, searches, and opens a document. Low priority,
mirrors the existing `manual` e2e.

---

## 7. Build order (→ plan)

1. `types.ts` + `api.ts` wrappers + `api.test.ts` cases
2. `Snippet.tsx` + test
3. `DocumentModal.tsx` + test
4. `SearchOverlay.tsx` + test
5. `App.tsx` wiring (button + shortcut) + `App.test.tsx` + `styles.css`
6. File the e2e follow-up bead; update doc 05 build-order note
