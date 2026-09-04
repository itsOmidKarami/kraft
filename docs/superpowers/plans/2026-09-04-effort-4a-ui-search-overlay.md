# Effort 4A-UI — search overlay + document viewer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a global search overlay to the SPA that queries the 4A `GET /search` endpoint and a document-viewer modal backed by `GET /documents/{id}`.

**Architecture:** Frontend-only. Two new modal components (`SearchOverlay`, `DocumentModal`) plus a tiny `Snippet` renderer, wired into `App` via a header button and a `Cmd/Ctrl-K` keydown listener. No store slice — search state is local component state (doc 05 §4.3: search results are a lagging shadow, never treated as live). `api.ts` gains `search()` / `getDocument()` wrappers over the existing `req()` helper.

**Tech Stack:** React 18, TypeScript, Vite, zustand (read-only use of the existing store for the repo-filter options), Vitest + Testing Library. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-04-effort-4a-ui-search-overlay-design.md`

## Global Constraints

- No new npm dependencies (spec §0, §3).
- Follow the existing SPA idiom: plain CSS in `frontend/src/styles.css`, `api.ts` wrappers over `req()`, modal-on-backdrop like `frontend/src/components/LogModal.tsx` / `IntakeModal.tsx`, `role="dialog"` a11y, local component state for self-fetched data like `HealthBadge` (spec §1, §5).
- Overlay, not a route (spec §2.1).
- Keyboard: `Cmd/Ctrl-K` opens, `Esc` closes. `/` is NOT bound (spec §2.3).
- Debounce search input 250 ms; empty query issues no request (spec §2.5).
- `mode` is always `fts` — do not send a `mode` param, do not build a mode selector (spec §1 "Out"; 4A backend 422s on other modes).
- Snippet rendering uses `[` / `]` markers → `<mark>`, plain-text split, never `dangerouslySetInnerHTML` (spec §2.6).
- Document body renders as raw markdown in `<pre class="doc-body">` — no markdown library (spec §1, deliberate simplification).
- `frontend/src` must stay free of Node globals — the app tsconfig has `types: []` (see `frontend/tsconfig.app.json`); tests get their types from `frontend/tsconfig.test.json`.
- Every task: `cd frontend && npx tsc -b` clean and `npm test` green before commit. Run `npx prettier`? No — the repo uses ruff for Python only; JS/TS formatting is not gated. Match surrounding style by hand.

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/src/types.ts` | +`SearchResult`, `SearchResponse`, `DocumentDetail` interfaces |
| `frontend/src/api.ts` | +`search(params)`, +`getDocument(id)` |
| `frontend/src/components/Snippet.tsx` | pure: `"[a] b [c]"` → React nodes with `<mark>` |
| `frontend/src/components/DocumentModal.tsx` | fetch + render one document (`GET /documents/{id}`) |
| `frontend/src/components/SearchOverlay.tsx` | query box + advanced filters + results list; hosts `DocumentModal` |
| `frontend/src/App.tsx` | header "Search" button, global `Cmd/Ctrl-K` / `Esc` listener, overlay mount |
| `frontend/src/styles.css` | `.search-overlay`, `.search-filters`, `.search-result`, `.doc-modal`, `.doc-body`, `.doc-meta`, `mark` |
| `frontend/src/components/Snippet.test.tsx` | Snippet |
| `frontend/src/components/DocumentModal.test.tsx` | DocumentModal |
| `frontend/src/components/SearchOverlay.test.tsx` | SearchOverlay |
| `frontend/src/api.test.ts` | +`search` / `getDocument` cases |
| `frontend/src/App.test.tsx` | +shortcut opens overlay |

---

## Task 1: types + api wrappers

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/api.test.ts`

**Interfaces:**
- Consumes: existing `req<T>()` in `api.ts`.
- Produces:
  - `SearchResult`, `SearchResponse`, `DocumentDetail` in `types.ts`
  - `api.search(params: { q: string; source_kind?: string; kind?: string; repo?: string }) => Promise<SearchResponse>`
  - `api.getDocument(id: string) => Promise<DocumentDetail>`

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/api.test.ts` inside the `describe("api", …)` block:

```ts
  it("search builds the query string and omits blank filters", async () => {
    const f = mockFetch(200, { query: "x", mode: "fts", results: [] });
    vi.stubGlobal("fetch", f);
    await api.search({ q: "reconnect backoff", kind: "specs", repo: "", source_kind: "" });
    const url = f.mock.calls[0][0] as string;
    expect(url).toContain("/search?");
    expect(url).toContain("q=reconnect+backoff");
    expect(url).toContain("kind=specs");
    expect(url).not.toContain("repo=");
    expect(url).not.toContain("source_kind=");
  });

  it("getDocument fetches by id and returns the row", async () => {
    vi.stubGlobal("fetch", mockFetch(200, { id: "d1", content: "# hi", metadata: {} }));
    const doc = await api.getDocument("d1");
    expect(doc.id).toBe("d1");
  });

  it("search surfaces the 422 detail from a bad FTS query", async () => {
    vi.stubGlobal("fetch", mockFetch(422, { detail: "bad search query: near \"x\"" }));
    await expect(api.search({ q: '"x' })).rejects.toThrow(/bad search query/);
  });
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd frontend && npm test -- api.test`
Expected: FAIL — `api.search is not a function`

- [ ] **Step 3: Add the types**

Append to `frontend/src/types.ts`:

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
  links: unknown[];
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

- [ ] **Step 4: Add the api wrappers**

In `frontend/src/api.ts`, add `SearchResponse` and `DocumentDetail` to the
existing type import from `./types`, then append:

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

- [ ] **Step 5: Run tests + typecheck**

Run: `cd frontend && npm test -- api.test && npx tsc -b`
Expected: PASS + clean

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(ui): api wrappers for /search and /documents/{id}"
```

---

## Task 2: Snippet component

**Files:**
- Create: `frontend/src/components/Snippet.tsx`
- Test: `frontend/src/components/Snippet.test.tsx`

**Interfaces:**
- Produces: `Snippet({ text }: { text: string }): JSX.Element` — renders `text`
  with `[` … `]` spans wrapped in `<mark>`. Unbalanced brackets render as
  literal text.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/Snippet.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Snippet } from "./Snippet";

describe("Snippet", () => {
  it("wraps [..] spans in <mark> and leaves the rest as text", () => {
    const { container } = render(<Snippet text="the [reconnect] [backoff] loop" />);
    const marks = container.querySelectorAll("mark");
    expect([...marks].map((m) => m.textContent)).toEqual(["reconnect", "backoff"]);
    expect(container.textContent).toBe("the reconnect backoff loop");
  });

  it("renders plain text when there are no markers", () => {
    render(<Snippet text="nothing to highlight" />);
    expect(screen.getByText("nothing to highlight")).toBeInTheDocument();
  });

  it("treats an unbalanced bracket as literal text", () => {
    const { container } = render(<Snippet text="a [b c" />);
    expect(container.querySelector("mark")).toBeNull();
    expect(container.textContent).toBe("a [b c");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- Snippet`
Expected: FAIL — cannot find `./Snippet`

- [ ] **Step 3: Implement**

Create `frontend/src/components/Snippet.tsx`:

```tsx
import { Fragment } from "react";

// FTS5 snippet() wraps matches in literal [ ] (see the 4A backend's
// snippet(documents_fts, 1, '[', ']', '…', 64) config). Split on a balanced
// [ ... ] pair; anything else is plain text.
const PART = /\[([^\]]+)\]/g;

export function Snippet({ text }: { text: string }) {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(PART)) {
    const i = m.index ?? 0;
    if (i > last) nodes.push(text.slice(last, i));
    nodes.push(<mark key={i}>{m[1]}</mark>);
    last = i + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return (
    <>
      {nodes.map((n, i) => (
        <Fragment key={i}>{n}</Fragment>
      ))}
    </>
  );
}
```

- [ ] **Step 4: Run test + typecheck**

Run: `cd frontend && npm test -- Snippet && npx tsc -b`
Expected: PASS + clean

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Snippet.tsx frontend/src/components/Snippet.test.tsx
git commit -m "feat(ui): Snippet — render FTS match markers as <mark>"
```

---

## Task 3: DocumentModal

**Files:**
- Create: `frontend/src/components/DocumentModal.tsx`
- Test: `frontend/src/components/DocumentModal.test.tsx`

**Interfaces:**
- Consumes: `api.getDocument`, `DocumentDetail`.
- Produces: `DocumentModal({ id, onClose }: { id: string; onClose: () => void }): JSX.Element`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/DocumentModal.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { DocumentModal } from "./DocumentModal";

const doc = {
  id: "d1",
  repo: "/r",
  source_kind: "artifact" as const,
  kind: "specs",
  title: "WS transport design",
  path: ".engineering/specs/ws.md",
  content: "# WS transport\n\nthe reconnect backoff schedule\n",
  metadata: { owner: "omid" },
  source_created_at: null,
  source_updated_at: "2026-09-03T00:00:00Z",
  indexed_at: "2026-09-04T00:00:00Z",
  links: [],
};

describe("DocumentModal", () => {
  it("renders the body and metadata", async () => {
    vi.spyOn(api, "getDocument").mockResolvedValue(doc);
    render(<DocumentModal id="d1" onClose={() => {}} />);
    expect(await screen.findByText(/the reconnect backoff schedule/)).toBeInTheDocument();
    expect(screen.getByText(".engineering/specs/ws.md")).toBeInTheDocument();
    expect(screen.getByText("specs")).toBeInTheDocument();
  });

  it("shows the error message on failure", async () => {
    vi.spyOn(api, "getDocument").mockRejectedValue(new Error("unknown document"));
    render(<DocumentModal id="nope" onClose={() => {}} />);
    expect(await screen.findByText(/unknown document/)).toBeInTheDocument();
  });

  it("calls onClose from the close button", async () => {
    vi.spyOn(api, "getDocument").mockResolvedValue(doc);
    const onClose = vi.fn();
    render(<DocumentModal id="d1" onClose={onClose} />);
    await screen.findByText(/reconnect backoff/);
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- DocumentModal`
Expected: FAIL — cannot find `./DocumentModal`

- [ ] **Step 3: Implement**

Create `frontend/src/components/DocumentModal.tsx`:

```tsx
import { useEffect, useState } from "react";
import * as api from "../api";
import type { DocumentDetail } from "../types";

export function DocumentModal({ id, onClose }: { id: string; onClose: () => void }) {
  const [doc, setDoc] = useState<DocumentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setDoc(null);
    setError(null);
    api
      .getDocument(id)
      .then((d) => live && setDoc(d))
      .catch((e) => live && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      live = false;
    };
  }, [id]);

  return (
    <div className="modal-backdrop" role="dialog" aria-label="document">
      <div className="modal doc-modal">
        <button className="doc-close" onClick={onClose}>
          close
        </button>
        {error && <p className="form-error">{error}</p>}
        {!doc && !error && <p>loading…</p>}
        {doc && (
          <>
            <h3>{doc.title}</h3>
            <dl className="doc-meta">
              <div><dt>kind</dt><dd>{doc.kind ?? "—"}</dd></div>
              <div><dt>source</dt><dd>{doc.source_kind}</dd></div>
              <div><dt>repo</dt><dd>{doc.repo}</dd></div>
              <div><dt>path</dt><dd>{doc.path}</dd></div>
              <div><dt>updated</dt><dd>{doc.source_updated_at ?? "—"}</dd></div>
            </dl>
            {/* ponytail: raw markdown, no renderer — spec/plan source is readable
                as-is. Add react-markdown if this proves painful. */}
            <pre className="doc-body">{doc.content}</pre>
          </>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run test + typecheck**

Run: `cd frontend && npm test -- DocumentModal && npx tsc -b`
Expected: PASS + clean

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/DocumentModal.tsx frontend/src/components/DocumentModal.test.tsx
git commit -m "feat(ui): DocumentModal — view one indexed document"
```

---

## Task 4: SearchOverlay

**Files:**
- Create: `frontend/src/components/SearchOverlay.tsx`
- Test: `frontend/src/components/SearchOverlay.test.tsx`

**Interfaces:**
- Consumes: `api.search`, `SearchResult`, `Snippet`, `DocumentModal`, the
  zustand store (read-only, for repo options).
- Produces: `SearchOverlay({ onClose }: { onClose: () => void }): JSX.Element`

- [ ] **Step 1: Write the failing test**

Create `frontend/src/components/SearchOverlay.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import { SearchOverlay } from "./SearchOverlay";

const hit = {
  id: "d1",
  repo: "/r",
  source_kind: "artifact" as const,
  kind: "specs",
  title: "WS transport design",
  path: ".engineering/specs/ws.md",
  snippet: "the [reconnect] [backoff] schedule",
  score: -1.5,
  links: [],
};

beforeEach(() => {
  useStore.setState({ workItems: {} } as never);
});
afterEach(() => vi.restoreAllMocks());

describe("SearchOverlay", () => {
  it("debounces, queries, and renders results with marks", async () => {
    const spy = vi.spyOn(api, "search").mockResolvedValue({
      query: "reconnect",
      mode: "fts",
      results: [hit],
    });
    render(<SearchOverlay onClose={() => {}} />);
    await userEvent.type(screen.getByRole("searchbox"), "reconnect");
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0][0]).toMatchObject({ q: "reconnect" });
    expect(await screen.findByText("WS transport design")).toBeInTheDocument();
    expect(screen.getByText("reconnect").tagName).toBe("MARK");
  });

  it("makes no request for an empty query", async () => {
    const spy = vi.spyOn(api, "search").mockResolvedValue({ query: "", mode: "fts", results: [] });
    render(<SearchOverlay onClose={() => {}} />);
    await userEvent.type(screen.getByRole("searchbox"), "ab");
    await userEvent.clear(screen.getByRole("searchbox"));
    await new Promise((r) => setTimeout(r, 300));
    expect(spy).not.toHaveBeenCalledWith(expect.objectContaining({ q: "" }));
  });

  it("adds the source_kind filter from the advanced panel", async () => {
    const spy = vi.spyOn(api, "search").mockResolvedValue({ query: "x", mode: "fts", results: [] });
    render(<SearchOverlay onClose={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: /advanced/i }));
    await userEvent.selectOptions(screen.getByLabelText("source_kind"), "session_summary");
    await userEvent.type(screen.getByRole("searchbox"), "x");
    await waitFor(() =>
      expect(spy).toHaveBeenCalledWith(
        expect.objectContaining({ q: "x", source_kind: "session_summary" }),
      ),
    );
  });

  it("shows an inline error when the query is rejected", async () => {
    vi.spyOn(api, "search").mockRejectedValue(new Error("bad search query"));
    render(<SearchOverlay onClose={() => {}} />);
    await userEvent.type(screen.getByRole("searchbox"), '"x');
    expect(await screen.findByText(/bad search query/)).toBeInTheDocument();
  });

  it("opens a document from a result and returns to the list", async () => {
    vi.spyOn(api, "search").mockResolvedValue({ query: "r", mode: "fts", results: [hit] });
    vi.spyOn(api, "getDocument").mockResolvedValue({
      ...hit,
      content: "# body\nfull text here",
      metadata: {},
      source_created_at: null,
      source_updated_at: null,
      indexed_at: "t",
    });
    render(<SearchOverlay onClose={() => {}} />);
    await userEvent.type(screen.getByRole("searchbox"), "reconnect");
    await userEvent.click(await screen.findByText("WS transport design"));
    expect(await screen.findByText(/full text here/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(await screen.findByText("WS transport design")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- SearchOverlay`
Expected: FAIL — cannot find `./SearchOverlay`

- [ ] **Step 3: Implement**

Create `frontend/src/components/SearchOverlay.tsx`:

```tsx
import { useEffect, useMemo, useRef, useState } from "react";
import { useStore } from "../store";
import type { SearchResult } from "../types";
import * as api from "../api";
import { DocumentModal } from "./DocumentModal";
import { Snippet } from "./Snippet";

const uniq = (xs: string[]) => [...new Set(xs)].sort();

export function SearchOverlay({ onClose }: { onClose: () => void }) {
  const repos = useStore((s) => uniq(Object.values(s.workItems).map((w) => w.repo)));
  const [q, setQ] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [sourceKind, setSourceKind] = useState("");
  const [kind, setKind] = useState("");
  const [repo, setRepo] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [openDoc, setOpenDoc] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    if (!q.trim()) {
      setResults([]);
      setError(null);
      return;
    }
    const t = setTimeout(() => {
      api
        .search({ q, source_kind: sourceKind, kind, repo })
        .then((r) => {
          setResults(r.results);
          setError(null);
        })
        .catch((e) => {
          setResults([]);
          setError(e instanceof Error ? e.message : String(e));
        });
    }, 250);
    return () => clearTimeout(t);
  }, [q, sourceKind, kind, repo]);

  const body = useMemo(() => {
    if (error) return <p className="form-error">{error}</p>;
    if (!q.trim()) return <p className="search-hint">type to search specs, plans, summaries</p>;
    if (!results.length) return <p className="search-hint">no matches</p>;
    return (
      <ul className="search-results">
        {results.map((r) => (
          <li key={r.id}>
            <button className="search-result" onClick={() => setOpenDoc(r.id)}>
              <span className="search-result-title">{r.title}</span>
              <span className="chip" data-kind={r.kind ?? ""}>{r.kind ?? r.source_kind}</span>
              <span className="repo-tag">{r.repo}</span>
              <span className="search-result-snippet"><Snippet text={r.snippet} /></span>
            </button>
          </li>
        ))}
      </ul>
    );
  }, [error, q, results]);

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="Search">
      <div className="modal search-overlay">
        <div className="search-bar">
          <input
            ref={inputRef}
            type="search"
            aria-label="search"
            placeholder="Search…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <button type="button" onClick={() => setAdvanced((v) => !v)}>
            advanced
          </button>
          <button type="button" onClick={onClose}>close</button>
        </div>

        {advanced && (
          <div className="search-filters">
            <label>source_kind
              <select aria-label="source_kind" value={sourceKind}
                onChange={(e) => setSourceKind(e.target.value)}>
                <option value="">any</option>
                <option value="artifact">artifact</option>
                <option value="session_summary">session_summary</option>
              </select>
            </label>
            <label>kind
              <input aria-label="kind" value={kind} onChange={(e) => setKind(e.target.value)} />
            </label>
            <label>repo
              <select aria-label="repo" value={repo} onChange={(e) => setRepo(e.target.value)}>
                <option value="">any repo</option>
                {repos.map((r) => <option key={r}>{r}</option>)}
              </select>
            </label>
          </div>
        )}

        {body}
      </div>
      {openDoc && <DocumentModal id={openDoc} onClose={() => setOpenDoc(null)} />}
    </div>
  );
}
```

> Executor note: `useRef` is imported from `react` (not `useRef` from a
> submodule). Fix the import line to `import { useEffect, useMemo, useRef, useState } from "react";`
> if the codegen split it wrong.

- [ ] **Step 4: Run test + typecheck**

Run: `cd frontend && npm test -- SearchOverlay && npx tsc -b`
Expected: PASS + clean

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SearchOverlay.tsx frontend/src/components/SearchOverlay.test.tsx
git commit -m "feat(ui): SearchOverlay — query, filters, results"
```

---

## Task 5: App wiring + styles

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/App.test.tsx`

**Interfaces:**
- Consumes: `SearchOverlay`.
- Produces: header `Search` button + `Cmd/Ctrl-K` global open, `Esc` close.

- [ ] **Step 1: Write the failing test**

Replace `frontend/src/App.test.tsx` with (keeps the existing case, adds two):

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { useStore } from "./store";
import { App } from "./App";

beforeEach(() => {
  useStore.setState({ workItems: {}, connection: "reconnecting" } as never);
  vi.spyOn(api, "getHealth").mockResolvedValue({
    status: "degraded",
    invalid_templates: { broken: "broken.yaml: bad hook" },
    invalid_policy: [],
  });
  vi.spyOn(api, "listWorkItems").mockResolvedValue({ items: [], cursor: 0 });
});

describe("App", () => {
  it("shows the reconnecting badge and a degraded-health flag", async () => {
    render(<App />);
    expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();
    expect(await screen.findByText(/broken/)).toBeInTheDocument();
  });

  it("opens the search overlay on Ctrl-K and closes it on Escape", async () => {
    render(<App />);
    await userEvent.keyboard("{Control>}k{/Control}");
    expect(screen.getByRole("dialog", { name: "Search" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Search" })).toBeNull();
  });

  it("opens the search overlay from the header button", async () => {
    render(<App />);
    await userEvent.click(screen.getByRole("button", { name: /search/i }));
    expect(screen.getByRole("dialog", { name: "Search" })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd frontend && npm test -- App.test`
Expected: FAIL — no dialog / no search button

- [ ] **Step 3: Wire `App.tsx`**

```tsx
import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { ConnBadge } from "./components/ConnBadge";
import { HealthBadge } from "./components/HealthBadge";
import { SearchOverlay } from "./components/SearchOverlay";
import { Board } from "./views/Board";
import { WorkItemDetail } from "./views/WorkItemDetail";

export function App() {
  const [search, setSearch] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearch(true);
      } else if (e.key === "Escape") {
        setSearch(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <BrowserRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <header className="app-header">
        <span className="brand">Kraft</span>
        <ConnBadge />
        <HealthBadge />
        <button className="search-open" onClick={() => setSearch(true)}>
          Search
        </button>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Board />} />
          <Route path="/work-items/:id" element={<WorkItemDetail />} />
        </Routes>
      </main>
      {search && <SearchOverlay onClose={() => setSearch(false)} />}
    </BrowserRouter>
  );
}
```

- [ ] **Step 4: Add styles**

Append to `frontend/src/styles.css`:

```css
/* search ---------------------------------------------------------- */
.search-open {
  margin-left: auto;
}

.search-overlay {
  width: min(720px, 92vw);
  max-width: none;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.search-bar {
  display: flex;
  gap: 8px;
}

.search-bar input[type="search"] {
  flex: 1;
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font: inherit;
}

.search-filters {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  font-size: 12px;
  color: var(--muted);
}

.search-hint {
  color: var(--muted);
}

.search-results {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
}

.search-result {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 4px 8px;
  width: 100%;
  text-align: left;
  padding: 8px;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: none;
  cursor: pointer;
  font: inherit;
}

.search-result:hover {
  background: var(--bg);
}

.search-result-title {
  font-weight: 600;
}

.search-result-snippet {
  grid-column: 1 / -1;
  color: var(--muted);
}

mark {
  background: #fef08a;
  color: inherit;
}

.doc-modal {
  width: min(820px, 92vw);
  max-width: none;
  max-height: 85vh;
  overflow-y: auto;
}

.doc-meta {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 2px 12px;
  font-size: 12px;
  color: var(--muted);
  margin: 8px 0;
}

.doc-meta div {
  display: contents;
}

.doc-meta dt {
  font-weight: 600;
}

.doc-meta dd {
  margin: 0;
  word-break: break-all;
}

.doc-body {
  white-space: pre-wrap;
  word-wrap: break-word;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 12px;
  font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
}
```

- [ ] **Step 5: Run tests + build**

Run: `cd frontend && npm test && npm run build`
Expected: all PASS, build succeeds

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/styles.css
git commit -m "feat(ui): mount search overlay — header button + Cmd/Ctrl-K"
```

---

## Task 6: e2e follow-up bead + doc note

**Files:**
- Modify: `docs/consolidated/05_ui.md` (§9 build order, mark step 7 partial)

- [ ] **Step 1: File the e2e bead**

```bash
bd create --title="Search UI Playwright e2e" --type=task --priority=3 --parent=Kraft-bj9 \
  --description="frontend/e2e/serve.py builds a fixture repo with no .engineering/ content. Add .engineering/*.md files to it and set KRAFT_INDEX_REPOS so the orchestrator indexes them, then a spec: open the overlay (Ctrl-K), search, open a document. Mirrors the existing manual/allow_failure e2e job."
```

- [ ] **Step 2: Annotate doc 05 §9**

In `docs/consolidated/05_ui.md`, build-order list, change step 7 to:

```markdown
7. **Search overlay + Document Viewer** (§4.3, §4.4) — ✅ **shipped** (Effort
   4A-UI): global overlay (`Cmd/Ctrl-K`), debounced `GET /search` (`fts`
   mode), advanced `source_kind`/`kind`/`repo` filters, results with match
   highlighting, click-through to a document modal (`GET /documents/{id}`,
   raw-markdown body). Deferred: `mode` selector (4C), `document_links`
   breadcrumb + linked-docs panel (4B), bead-scoped search box (`06` §6.1),
   markdown rendering.
```

- [ ] **Step 3: Commit**

```bash
git add docs/consolidated/05_ui.md
git commit -m "docs: mark UI build-order step 7 (search) shipped"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| §1 global overlay (query, debounce, empty state) | Task 4 |
| §1 advanced filters (source_kind/kind/repo) | Task 4 |
| §1 results list (title, kind badge, repo, snippet marks) | Task 4 + Task 2 |
| §1 document viewer (metadata + raw body) | Task 3 |
| §1 "back to results without re-query" | Task 4 (`openDoc` state) |
| §2.1 overlay not route | Task 4/5 (modal, no `<Route>`) |
| §2.2 no store slice | Task 4 (local state; store read-only for repos) |
| §2.3 Cmd/Ctrl-K open, Esc close, no `/` | Task 5 |
| §2.4 repo options from work-item store | Task 4 (`uniq(...workItems.map(repo))`) |
| §2.5 250 ms debounce, min length 1, empty → no request | Task 4 |
| §2.6 `[..]`→`<mark>`, no `dangerouslySetInnerHTML` | Task 2 |
| §3 file list | Tasks 1–5 |
| §4 interfaces (types, api, component props) | Tasks 1, 3, 4 |
| §4 raw-markdown `<pre>` body | Task 3 |
| §5 a11y (`role="dialog"`, `aria-modal`, button results, autofocus) | Tasks 3, 4 |
| §6 test matrix | Tasks 2–5 |
| §6 e2e follow-up bead | Task 6 |
| §1 "Out" list — nothing from it implemented | verified: no mode selector, no links UI, no `/beads/search`, no markdown lib |

No gaps.

**Placeholder scan:** one "Executor note" in Task 4 about the `useRef`
import line — a concrete fix instruction, not a TBD. All code steps carry
full code. Error/empty/loading states are all written out, not hand-waved.

**Type consistency:**
- `SearchResult` / `SearchResponse` / `DocumentDetail` — defined Task 1,
  consumed identically in Tasks 3 (`DocumentDetail`) and 4 (`SearchResult`,
  `SearchResponse` via `api.search`). ✔
- `api.search(params)` param object `{ q, source_kind?, kind?, repo? }` —
  Task 1 definition matches Task 4 call sites and Task 1 tests. ✔
- `api.getDocument(id)` → `DocumentDetail` — Task 1 def, Task 3 use. ✔
- `SearchOverlay({ onClose })` / `DocumentModal({ id, onClose })` /
  `Snippet({ text })` — props match between component defs, their tests, and
  the mount sites in Task 4 (`DocumentModal`) and Task 5 (`SearchOverlay`). ✔
- CSS class names (`.search-overlay`, `.search-result`, `.doc-body`,
  `.doc-meta`, `mark`) — Task 5 styles match the `className`s emitted in
  Tasks 3/4. ✔

**Execution:** Inline. One cohesive frontend slice, five files that
reference each other, established SPA patterns — a single implementer
holding the whole thing in context beats cold-starting a subagent per task.
