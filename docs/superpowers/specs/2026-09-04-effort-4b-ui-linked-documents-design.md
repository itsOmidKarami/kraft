# Effort 4B-UI — Linked Documents Panel + Document Link Breadcrumbs

**Status:** approved
**Date:** 2026-09-04
**Bead:** Kraft-bj9.8 (child of Kraft-bj9)
**Component:** 4 — UI (`docs/consolidated/05_ui.md` §4.2, §4.3, §4.4)
**Depends on:** Kraft-bj9.2 (Effort 4B backend)

---

## 1. What this is

4B put session summaries in the index, populated `document_links`, and started
resolving links inline. Nothing in the SPA reads any of it. This effort wires
the three places doc 05 says those links belong:

1. **Linked documents panel** on Work Item Detail (§4.2), fed by
   `GET /work-items/{id}/documents`.
2. **Resolved links in the Document Viewer's metadata panel** (§4.4).
3. **Breadcrumb from a document back to its work item(s)** (§4.3), so a search
   hit is a navigation path and not a dead end.

Bounded change: three existing files plus one new component. No new routes, no
state-shape changes beyond typing what is already there.

## 2. Decisions

**`links: unknown[]` becomes a real type.** `frontend/src/types.ts` carries
`links: unknown[]` on both `SearchResult` and `DocumentDetail` — a 4A placeholder
for rows that did not exist yet. Now they do:

```ts
export interface DocumentLink {
  work_item_id: string | null;
  node_id: string | null;
  hook_point: string | null;
  worker_session_id: string | null;
}
```

**The panel owns its own data, not the store.** Linked documents are a per-view
read, not shared normalized state reduced from the event stream. Fetching in the
component keeps the store's one job — event-stream projection — intact. The panel
refetches when the item's event count changes, which is how it notices a new
session summary arriving mid-chain without inventing a second polling loop.

**Breadcrumbs navigate and dismiss.** A work-item link inside the Document Viewer
routes to `/work-items/<id>` and closes the viewer *and* the search overlay above
it. A modal left open over a route change is the bug this avoids.

**No link chips in the results list.** The bead's title floated them, but doc 05
§4.3 puts the breadcrumb in the viewer, and a results row already carries title,
kind badge, repo and snippet. Adding a fourth kind of metadata there costs
scannability. Skipped deliberately.

**Lagging-shadow rule holds (§4.3, `04` §8).** Nothing rendered from a document
or a search hit is treated as current. The breadcrumb navigates to
`/work-items/{id}`, which re-fetches live state; it never displays status or gate
information carried on the document.

## 3. Components

### 3.1 `frontend/src/types.ts`
Add `DocumentLink`; replace `links: unknown[]` with `links: DocumentLink[]` on
`SearchResult` and `DocumentDetail`. Add `WorkItemDocument`:

```ts
export interface WorkItemDocument {
  document_id: string;
  repo: string;
  title: string;
  kind: string | null;
  source_kind: string;
  path: string;
  node_id: string | null;
  hook_point: string | null;
  worker_session_id: string | null;
}
```

### 3.2 `frontend/src/api.ts`
`getWorkItemDocuments(id): Promise<{ work_item_id: string; documents: WorkItemDocument[] }>`.

### 3.3 `frontend/src/components/LinkedDocuments.tsx` (new)
Props `{ workItemId: string; eventCount: number }`. Fetches on mount and whenever
`eventCount` changes. Renders nothing but a quiet empty state when the list is
empty — a work item with no documents yet is the normal early state, not an error.
Each row is a button showing title, a `kind` chip, the path, and the node it came
from where known; clicking opens `DocumentModal`.

### 3.4 `frontend/src/views/WorkItemDetail.tsx`
Render `<LinkedDocuments>` between `CurrentNodePanel` and `EventTimeline` —
documents are about the work done, so they sit with the work, above the log.

### 3.5 `frontend/src/components/DocumentModal.tsx`
Add a "linked to" row in the metadata list: work-item ids as router links, plus
the node / hook point / session where present. Accepts an optional
`onNavigate?: () => void` fired before routing, so a parent overlay can close
itself.

### 3.6 `frontend/src/components/SearchOverlay.tsx`
Pass `onNavigate={onClose}` to `DocumentModal` so following a breadcrumb dismisses
the whole search stack.

## 4. Error handling

| Case | Behavior |
|---|---|
| `GET /work-items/{id}/documents` fails | Panel shows a single inline error line; the rest of the detail view is unaffected. |
| A 404 (unknown work item) | Same inline error. The route already guards unknown items above this panel. |
| Empty `documents` | Quiet empty state, no error styling. |
| A link with `work_item_id: null` | Rendered as node/session metadata only — no dead link. |
| Component unmounts mid-fetch | Ignored via the existing `live` flag pattern in `DocumentModal`. |

## 5. Testing

Vitest + Testing Library, matching the existing component tests:

- `LinkedDocuments`: renders returned documents; opens the viewer on click;
  shows the empty state for `[]`; shows an error line on rejection; refetches
  when `eventCount` changes.
- `DocumentModal`: renders work-item breadcrumbs from `links`; a link fires
  `onNavigate`; a document whose links carry no `work_item_id` renders session
  metadata and no link.
- `WorkItemDetail`: the panel is present and sits above the event timeline.
- `SearchOverlay`: opening a document and following its breadcrumb closes the
  overlay.

## 6. Out of scope

- Link chips in the search results list (§2).
- `mode=vector|hybrid` and the mode radio — 4C ships the backend for those.
- The bead-scoped search box (§4.3) — component 5, `GET /beads/search`.
- Markdown rendering; the viewer stays a raw `<pre>` until someone asks.
