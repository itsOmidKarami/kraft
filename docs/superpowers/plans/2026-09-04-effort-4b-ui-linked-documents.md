# Effort 4B-UI — Linked Documents: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Surface 4B's `document_links` in the SPA — a linked-documents panel on Work Item Detail, resolved links in the Document Viewer, and a breadcrumb from a document back to its work item.

**Architecture:** Three existing files plus one new component. The panel fetches its own data (linked documents are a per-view read, not event-stream-derived state) and refetches when the item's event count changes.

**Tech Stack:** React 18, TypeScript, react-router-dom, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-09-04-effort-4b-ui-linked-documents-design.md`

## Global Constraints

- Frontend only. No backend changes — 4B shipped every endpoint this needs.
- Test command: `cd frontend && npm test`. Lint: `npm run lint`.
- `DocumentModal` gains a router `<Link>`, so **every test rendering it must wrap in `<MemoryRouter>`** — including the existing ones.
- Lagging-shadow rule (doc 05 §4.3): never render status/gate state off a document or search hit. Breadcrumbs navigate; the destination re-fetches.

---

### Task 1: Type the links and add the API call

**Files:**
- Modify: `frontend/src/types.ts`, `frontend/src/api.ts`
- Test: `frontend/src/api.test.ts`

**Interfaces:**
- Produces: `DocumentLink`, `WorkItemDocument`, `getWorkItemDocuments(id)`.

- [ ] **Step 1: Write the failing test** — append to `frontend/src/api.test.ts`, matching the file's existing fetch-mock style:

```ts
it("getWorkItemDocuments fetches the work item's documents", async () => {
  const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ work_item_id: "w1", documents: [] }), {
      headers: { "content-type": "application/json" },
    }),
  );
  const body = await api.getWorkItemDocuments("w1");
  expect(body.work_item_id).toBe("w1");
  expect(fetchMock.mock.calls[0][0]).toBe("/work-items/w1/documents");
});
```

- [ ] **Step 2: Run it and watch it fail** — `cd frontend && npm test -- api.test` → FAIL, `getWorkItemDocuments is not a function`
- [ ] **Step 3: Implement** — in `types.ts`:

```ts
export interface DocumentLink {
  work_item_id: string | null;
  node_id: string | null;
  hook_point: string | null;
  worker_session_id: string | null;
}

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

Replace `links: unknown[]` with `links: DocumentLink[]` on both `SearchResult` and `DocumentDetail`. In `api.ts`, import `WorkItemDocument` and add:

```ts
export const getWorkItemDocuments = (id: string) =>
  req<{ work_item_id: string; documents: WorkItemDocument[] }>(
    `/work-items/${encodeURIComponent(id)}/documents`,
  );
```

- [ ] **Step 4: Run tests** — `npm test` → PASS
- [ ] **Step 5: Commit** — `feat(Kraft-bj9.8): type document links and add getWorkItemDocuments`

---

### Task 2: The linked documents panel

**Files:**
- Create: `frontend/src/components/LinkedDocuments.tsx`, `frontend/src/components/LinkedDocuments.test.tsx`
- Modify: `frontend/src/views/WorkItemDetail.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: `getWorkItemDocuments`, `WorkItemDocument` (Task 1), `DocumentModal`.
- Produces: `<LinkedDocuments workItemId={string} eventCount={number} />`.

- [ ] **Step 1: Write the failing tests** — `LinkedDocuments.test.tsx` covering: renders returned docs; empty state on `[]`; error line on rejection; opens `DocumentModal` on click; refetches when `eventCount` changes. Wrap renders in `<MemoryRouter>`.
- [ ] **Step 2: Run and watch fail** — module does not exist
- [ ] **Step 3: Implement** the component: `useEffect` keyed on `[workItemId, eventCount]` with the `live` flag pattern from `DocumentModal`; local `openDoc` state; render `<DocumentModal>` when set.
- [ ] **Step 4: Wire it in** — `WorkItemDetail.tsx` renders `<LinkedDocuments workItemId={id} eventCount={events.length} />` between `<CurrentNodePanel>` and `<EventTimeline>`. Add the matching test assertion to `WorkItemDetail.test.tsx`.
- [ ] **Step 5: Run tests, commit** — `feat(Kraft-bj9.8): linked documents panel on work item detail`

---

### Task 3: Breadcrumbs in the Document Viewer

**Files:**
- Modify: `frontend/src/components/DocumentModal.tsx`, `frontend/src/components/DocumentModal.test.tsx`, `frontend/src/components/SearchOverlay.tsx`, `frontend/src/components/SearchOverlay.test.tsx`

**Interfaces:**
- Consumes: `DocumentLink` (Task 1).
- Produces: `DocumentModal` gains optional `onNavigate?: () => void`.

- [ ] **Step 1: Write the failing tests** — breadcrumbs render from `links`; clicking one fires `onNavigate`; links with `work_item_id: null` render session metadata and no link. Wrap **all** `DocumentModal` renders (existing ones too) in `<MemoryRouter>`.
- [ ] **Step 2: Run and watch fail**
- [ ] **Step 3: Implement** — a "linked to" `<div>` in the `doc-meta` list: unique non-null `work_item_id`s as `<Link to={`/work-items/${id}`} onClick={onNavigate}>`, then node / hook point / session where present.
- [ ] **Step 4: Wire the overlay** — `SearchOverlay` passes `onNavigate={onClose}`; add the test asserting the overlay closes.
- [ ] **Step 5: Run the full frontend suite, lint, commit** — `feat(Kraft-bj9.8): document link breadcrumbs back to work items`

---

### Task 4: Close out

- [ ] `cd frontend && npm test && npm run lint && npm run build`
- [ ] `uv run pytest -m "not e2e and not slow" -q` (unchanged, but confirms nothing backend regressed)
- [ ] `bd close Kraft-bj9.8`
- [ ] Commit and push

## Self-Review

**Spec coverage:** §3.1→Task 1, §3.2→Task 1, §3.3→Task 2, §3.4→Task 2, §3.5→Task 3, §3.6→Task 3, §4 error cases→Task 2 tests, §5→every task.

**Type consistency:** `WorkItemDocument.document_id` matches the backend's `SELECT d.id AS document_id`; `DocumentLink` fields match `_LINK_COLS` in `src/kraft/index/service.py`.

**Caution:** `DocumentModal` acquires a router dependency in Task 3. Its existing tests render it bare and will fail until wrapped.
