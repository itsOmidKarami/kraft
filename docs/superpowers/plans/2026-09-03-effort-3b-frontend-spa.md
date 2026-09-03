# Effort 3B — React SPA Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local React SPA — Board, New Work Item modal, Work Item Detail (chain stepper, current-node panel, event timeline, log modal), inline Gates — served by the orchestrator's FastAPI process and kept live over `WS /ws/events`.

**Architecture:** Single-page React app, no meta-framework. One global WebSocket opened at boot after a `GET /work-items` bootstrap; every frame reduces into a normalized Zustand store keyed by `work_item_id`. REST (`GET /work-items/{id}`) is the authoritative hydrate on the Detail view plus a 60s reconciliation refetch. Build output (`frontend/dist/`) is static assets served by FastAPI (Effort 3A Task 6).

**Tech Stack:** Vite, React 18, TypeScript, `zustand`, `react-router-dom`, Vitest + `@testing-library/react` + `jsdom`, `@playwright/test`.

**Spec:** `docs/superpowers/specs/2026-09-03-effort-3-ws-transport-react-ui-design.md` (§3, §4, §5, §6.3, §6.4, §6.5; chunk 3B of §7)

**Depends on:** `docs/superpowers/plans/2026-09-03-effort-3a-ws-backend.md` (must be merged — provides `WS /ws/events`, `GET /work-items`, `GET /templates`, SPA serving).

## Global Constraints

- Node 20 LTS; npm (lockfile `frontend/package-lock.json` committed).
- TypeScript `strict: true`.
- No component library, no CSS framework — plain CSS in `src/styles.css` (spec §3.1, doc `05` §1).
- Dependencies limited to those in the Tech Stack line above. No date libs, no icon packs, no state lib other than `zustand`.
- All API paths are same-origin relative (`/work-items`, `/ws/events`, …) — the app is served by the orchestrator. `vite.config.ts` proxies them to `http://127.0.0.1:8765` for `npm run dev` only.
- No auth — the app assumes `127.0.0.1` binding (spec §1 "Out"). No `/login` screen, no 401 handling.
- Event frame shape from the server (spec §2.3): `{ seq: number, work_item_id: string, type: string, payload: Record<string, unknown>, created_at: string }`.
- Every task ends with `npm test` green (from `frontend/`) and a commit.

---

## File Structure

| File | Responsibility |
|---|---|
| `frontend/package.json`, `vite.config.ts`, `tsconfig.json`, `index.html` | Build + test config. |
| `frontend/src/main.tsx` | Router, boot sequence (bootstrap → open WS), top-level `<App>`. |
| `frontend/src/types.ts` | `WorkItem`, `WorkerSession`, `ChainNode`, `ChainDefinition`, `KraftEvent`, `Health`. |
| `frontend/src/api.ts` | Typed `fetch` wrappers for every REST endpoint. No React. |
| `frontend/src/store.ts` | Zustand store: `workItems`, `sessionsByItem`, `eventsByItem`, `lastSeq`, `connection`; `bootstrap`, `hydrateItem`, `applyEvent`. |
| `frontend/src/ws.ts` | `connectEvents()` — one WS, `onmessage → store.applyEvent`, reconnect-with-backoff using current `lastSeq`. No React. |
| `frontend/src/components/ChainStrip.tsx` | Chain graph — nodes in order, current highlighted, gate ⚑, task-count / fix-cycle badges. `size: 'sm' \| 'lg'`. Shared by Board card and Detail stepper. |
| `frontend/src/components/CurrentNodePanel.tsx` | Session rows + status chips for the current node; infers "awaiting gate". |
| `frontend/src/components/EventTimeline.tsx` | Reverse-chron event list; session events link to `<LogModal>`. |
| `frontend/src/components/LogModal.tsx` | Pull `GET /worker-sessions/{id}/log` into a `<pre>`. |
| `frontend/src/components/Gate.tsx` | Approve / Reject (+ required note). |
| `frontend/src/components/IntakeModal.tsx` | New Work Item form. |
| `frontend/src/components/ConnBadge.tsx`, `HealthBadge.tsx` | Connection state / degraded-health indicators. |
| `frontend/src/views/Board.tsx`, `views/WorkItemDetail.tsx` | The two routes. |
| `frontend/src/*.test.ts(x)` | Vitest specs, co-located. |
| `frontend/e2e/chain.spec.ts` | Playwright: create a work item in the UI, watch it reach `completed`. |
| `.gitlab-ci.yml` (modify) | `frontend` job. |
| `.gitignore` (modify) | `frontend/node_modules`, `frontend/dist`, `frontend/playwright-report`, `frontend/test-results`. |

---

## Task 1: Scaffold the frontend project

**Files:**
- Create: `frontend/package.json`, `frontend/package-lock.json` (generated), `frontend/vite.config.ts`, `frontend/tsconfig.json`, `frontend/tsconfig.node.json`, `frontend/index.html`, `frontend/src/main.tsx`, `frontend/src/styles.css`, `frontend/src/smoke.test.ts`
- Modify: `.gitignore`
- Modify: `.gitlab-ci.yml`

**Interfaces:**
- Produces: `npm run build` → `frontend/dist/index.html` + `frontend/dist/assets/*`; `npm test` runs Vitest headless; `npm run dev` serves on `:5173` proxying API calls to `:8765`.

- [ ] **Step 1: Create `frontend/package.json`**

```json
{
  "name": "kraft-ui",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "e2e": "playwright test"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "react-router-dom": "^6.26.0",
    "zustand": "^4.5.0"
  },
  "devDependencies": {
    "@playwright/test": "^1.47.0",
    "@testing-library/jest-dom": "^6.4.0",
    "@testing-library/react": "^16.0.0",
    "@testing-library/user-event": "^14.5.0",
    "@types/react": "^18.3.0",
    "@types/react-dom": "^18.3.0",
    "@vitejs/plugin-react": "^4.3.0",
    "jsdom": "^25.0.0",
    "typescript": "^5.5.0",
    "vite": "^5.4.0",
    "vitest": "^2.1.0"
  }
}
```

- [ ] **Step 2: Create `frontend/tsconfig.json` and `frontend/tsconfig.node.json`**

`tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noEmit": true,
    "skipLibCheck": true,
    "types": ["vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src", "e2e"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

`tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "skipLibCheck": true,
    "strict": true
  },
  "include": ["vite.config.ts"]
}
```

- [ ] **Step 3: Create `frontend/vite.config.ts`**

```ts
/// <reference types="vitest" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const API = "http://127.0.0.1:8765";
const proxy = Object.fromEntries(
  ["/work-items", "/templates", "/health", "/worker-sessions"].map((p) => [
    p,
    { target: API, changeOrigin: true },
  ]),
);
proxy["/ws"] = { target: API, ws: true, changeOrigin: true };

export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "dist" },
  server: { port: 5173, proxy },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test-setup.ts"],
    exclude: ["e2e/**", "node_modules/**"],
  },
});
```

- [ ] **Step 4: Create `frontend/src/test-setup.ts`**

```ts
import "@testing-library/jest-dom/vitest";
```

- [ ] **Step 5: Create `frontend/index.html`**

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Kraft</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 6: Create `frontend/src/main.tsx` (temporary stub) and `frontend/src/styles.css` (empty)**

```tsx
import React from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <h1>Kraft</h1>
  </React.StrictMode>,
);
```

- [ ] **Step 7: Create `frontend/src/smoke.test.ts`**

```ts
import { describe, expect, it } from "vitest";

describe("smoke", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 8: Install + build + test**

Run:
```bash
cd frontend && npm install && npm run build && npm test
```
Expected: `npm install` writes `package-lock.json`; `dist/index.html` created; Vitest reports 1 passed.

- [ ] **Step 9: Update `.gitignore`** — append:

```
frontend/node_modules/
frontend/dist/
frontend/playwright-report/
frontend/test-results/
```

- [ ] **Step 10: Update `.gitlab-ci.yml`** — add a job (does not share the Python `default` image):

```yaml
frontend:
  image: node:20-bookworm-slim
  cache:
    key:
      files:
        - frontend/package-lock.json
    paths:
      - frontend/node_modules
  before_script:
    - cd frontend && npm ci
  script:
    - npm run build
    - npm test
```

- [ ] **Step 11: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vite.config.ts \
  frontend/tsconfig.json frontend/tsconfig.node.json frontend/index.html \
  frontend/src/main.tsx frontend/src/styles.css frontend/src/test-setup.ts \
  frontend/src/smoke.test.ts .gitignore .gitlab-ci.yml
git commit -m "chore(frontend): Vite + React + TS + Vitest scaffold"
```

---

## Task 2: Types + API client

**Files:**
- Create: `frontend/src/types.ts`, `frontend/src/api.ts`, `frontend/src/api.test.ts`

**Interfaces:**
- Produces:
  - `types.ts`:
    ```ts
    export interface ChainNode { id: string; tasks: string[]; gate_after: string | null; fix_loop?: string }
    export interface ChainDefinition { template_id: string; nodes: ChainNode[] }
    export type WorkItemStatus = "active" | "needs_human" | "completed";
    export interface WorkItem {
      id: string; title: string; repo: string; status: WorkItemStatus;
      chain_template: string; chain_definition: ChainDefinition;
      current_node_id: string | null; bead_id: string | null;
      created_at: string; updated_at: string;
      // client-derived, not from the list endpoint:
      pendingGate?: string | null; rejectNote?: string | null; fixCycle?: number;
      completedNodes?: string[];
    }
    export type SessionStatus =
      | "pending" | "running" | "done" | "failed" | "capped_out" | "paused" | "unknown";
    export interface WorkerSession {
      id: string; work_item_id: string; node_id: string; hook_point: string;
      status: SessionStatus; attempt: number; created_at: string; exited_at: string | null;
    }
    export interface KraftEvent {
      seq: number; work_item_id: string; type: string;
      payload: Record<string, unknown>; created_at: string;
    }
    export interface Health {
      status: "ok" | "degraded";
      invalid_templates: string[]; invalid_policy: string[];
    }
    ```
  - `api.ts`:
    ```ts
    listWorkItems(): Promise<{ items: WorkItem[]; cursor: number }>
    getWorkItem(id): Promise<WorkItem & { worker_sessions: WorkerSession[] }>
    getEvents(id, afterSeq?): Promise<KraftEvent[]>
    createWorkItem(body: { repo: string; title: string; chain_template?: string }): Promise<{ id: string }>
    approveGate(id, gate): Promise<void>
    rejectGate(id, gate, note): Promise<void>
    getTemplates(): Promise<{ id: string }[]>
    getHealth(): Promise<Health>
    logUrl(sessionId): string
    ```
    Non-2xx → `throw new Error(body.detail ?? res.statusText)`.

- [ ] **Step 1: Write the failing test — `frontend/src/api.test.ts`**

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: "x",
    json: async () => body,
    text: async () => JSON.stringify(body),
  });
}

afterEach(() => vi.restoreAllMocks());

describe("api", () => {
  it("listWorkItems returns items + cursor", async () => {
    vi.stubGlobal("fetch", mockFetch(200, { items: [], cursor: 7 }));
    expect(await api.listWorkItems()).toEqual({ items: [], cursor: 7 });
  });

  it("createWorkItem posts JSON and returns the id", async () => {
    const f = mockFetch(201, { id: "abc" });
    vi.stubGlobal("fetch", f);
    const out = await api.createWorkItem({ repo: "/r", title: "t" });
    expect(out.id).toBe("abc");
    expect(f).toHaveBeenCalledWith(
      "/work-items",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("throws with the server detail on non-2xx", async () => {
    vi.stubGlobal("fetch", mockFetch(422, { detail: "bad repo" }));
    await expect(api.createWorkItem({ repo: "/nope", title: "t" })).rejects.toThrow(
      "bad repo",
    );
  });

  it("rejectGate sends the note", async () => {
    const f = mockFetch(200, {});
    vi.stubGlobal("fetch", f);
    await api.rejectGate("id1", "spec_approval", "redo");
    expect(f).toHaveBeenCalledWith(
      "/work-items/id1/gates/spec_approval/reject",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ note: "redo" }),
      }),
    );
  });

  it("logUrl builds the log path", () => {
    expect(api.logUrl("s9")).toBe("/worker-sessions/s9/log");
  });
});
```

- [ ] **Step 2: Run — expect FAIL** (`Cannot find module './api'`).

Run: `cd frontend && npx vitest run src/api.test.ts`

- [ ] **Step 3: Implement `frontend/src/types.ts`** (exactly the interfaces block above).

- [ ] **Step 4: Implement `frontend/src/api.ts`**

```ts
import type { Health, KraftEvent, WorkItem, WorkerSession } from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json())?.detail ?? detail;
    } catch {
      /* non-JSON body */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const listWorkItems = () =>
  req<{ items: WorkItem[]; cursor: number }>("/work-items");

export const getWorkItem = (id: string) =>
  req<WorkItem & { worker_sessions: WorkerSession[] }>(`/work-items/${id}`);

export const getEvents = (id: string, afterSeq = 0) =>
  req<KraftEvent[]>(`/work-items/${id}/events?after_seq=${afterSeq}`);

export const createWorkItem = (body: {
  repo: string;
  title: string;
  chain_template?: string;
}) => req<{ id: string }>("/work-items", json("POST", body));

export const approveGate = (id: string, gate: string) =>
  req<void>(`/work-items/${id}/gates/${gate}/approve`, { method: "POST" });

export const rejectGate = (id: string, gate: string, note: string) =>
  req<void>(`/work-items/${id}/gates/${gate}/reject`, json("POST", { note }));

export const getTemplates = () => req<{ id: string }[]>("/templates");

export const getHealth = () => req<Health>("/health");

export const logUrl = (sessionId: string) => `/worker-sessions/${sessionId}/log`;
```

- [ ] **Step 4b:** Note the API mismatch — the 3A `approve`/`reject` handlers return a JSON body, not 204. `req` handles both (`res.ok` + `json()`); the `void` cast is intentional. `createWorkItem` reads only `.id` from the 201 body.

- [ ] **Step 5: Run — expect PASS.** `cd frontend && npx vitest run src/api.test.ts`

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat(frontend): typed API client"
```

---

## Task 3: Store + event reducer

**Files:**
- Create: `frontend/src/store.ts`, `frontend/src/store.test.ts`

**Interfaces:**
- Consumes: `api` (Task 2), `types` (Task 2).
- Produces: `useStore` (zustand hook) with state `{ workItems, sessionsByItem, eventsByItem, lastSeq, connection }` and actions `bootstrap()`, `hydrateItem(id)`, `applyEvent(ev)`, `setConnection(c)`. Reducer table = spec §3.2.

- [ ] **Step 1: Write the failing test — `frontend/src/store.test.ts`**

```ts
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "./store";
import type { KraftEvent, WorkItem } from "./types";

const baseItem = (over: Partial<WorkItem> = {}): WorkItem => ({
  id: "w1",
  title: "t",
  repo: "/r",
  status: "active",
  chain_template: "quick-task",
  chain_definition: {
    template_id: "quick-task",
    nodes: [
      { id: "env_setup", tasks: ["on.env.prepare"], gate_after: null },
      { id: "verify", tasks: ["on.test.run"], gate_after: null },
    ],
  },
  current_node_id: "env_setup",
  bead_id: "B-1",
  created_at: "t",
  updated_at: "t",
  ...over,
});

const ev = (over: Partial<KraftEvent>): KraftEvent => ({
  seq: 1,
  work_item_id: "w1",
  type: "node_started",
  payload: {},
  created_at: "t",
  ...over,
});

beforeEach(() => {
  useStore.setState({
    workItems: { w1: baseItem() },
    sessionsByItem: {},
    eventsByItem: {},
    lastSeq: 0,
    connection: "connecting",
  });
  vi.restoreAllMocks();
});

describe("applyEvent", () => {
  it("node_started sets current_node_id and bumps lastSeq", () => {
    useStore.getState().applyEvent(ev({ seq: 5, type: "node_started", payload: { node_id: "verify" } }));
    const s = useStore.getState();
    expect(s.workItems.w1.current_node_id).toBe("verify");
    expect(s.lastSeq).toBe(5);
    expect(s.eventsByItem.w1).toHaveLength(1);
  });

  it("node_completed records the node as completed", () => {
    useStore.getState().applyEvent(ev({ type: "node_completed", payload: { node_id: "env_setup" } }));
    expect(useStore.getState().workItems.w1.completedNodes).toContain("env_setup");
  });

  it("worker_session_started then _exited upsert one session row", () => {
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 2, type: "worker_session_started", payload: { session_id: "s1", node_id: "env_setup", hook_point: "on.env.prepare" } }));
    st.applyEvent(ev({ seq: 3, type: "worker_session_exited", payload: { session_id: "s1", status: "done" } }));
    const rows = useStore.getState().sessionsByItem.w1;
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe("done");
  });

  it("gate_requested / gate_approved toggle pendingGate", () => {
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 2, type: "gate_requested", payload: { gate: "spec_approval" } }));
    expect(useStore.getState().workItems.w1.pendingGate).toBe("spec_approval");
    st.applyEvent(ev({ seq: 3, type: "gate_approved", payload: { gate: "spec_approval" } }));
    expect(useStore.getState().workItems.w1.pendingGate).toBeNull();
    expect(useStore.getState().workItems.w1.status).toBe("active");
  });

  it("gate_rejected keeps the note", () => {
    useStore.getState().applyEvent(ev({ type: "gate_rejected", payload: { gate: "spec_approval", note: "nope" } }));
    expect(useStore.getState().workItems.w1.rejectNote).toBe("nope");
    expect(useStore.getState().workItems.w1.pendingGate).toBeNull();
  });

  it("fix_cycle_started sets the badge", () => {
    useStore.getState().applyEvent(ev({ type: "fix_cycle_started", payload: { node_id: "verify", cycle: 2 } }));
    expect(useStore.getState().workItems.w1.fixCycle).toBe(2);
  });

  it("work_item_completed / needs_human set status", () => {
    useStore.getState().applyEvent(ev({ type: "work_item_completed", payload: {} }));
    expect(useStore.getState().workItems.w1.status).toBe("completed");
  });

  it("unknown type is stored in the timeline, no throw", () => {
    expect(() =>
      useStore.getState().applyEvent(ev({ type: "some_future_event", payload: {} })),
    ).not.toThrow();
    expect(useStore.getState().eventsByItem.w1).toHaveLength(1);
  });

  it("work_item_created for an unknown id triggers hydrateItem", async () => {
    const spy = vi
      .spyOn(useStore.getState(), "hydrateItem")
      .mockResolvedValue(undefined);
    useStore.getState().applyEvent(ev({ work_item_id: "w2", type: "work_item_created", payload: {} }));
    expect(spy).toHaveBeenCalledWith("w2");
  });

  it("lastSeq never goes backward", () => {
    const st = useStore.getState();
    st.applyEvent(ev({ seq: 10 }));
    st.applyEvent(ev({ seq: 4 }));
    expect(useStore.getState().lastSeq).toBe(10);
  });
});

describe("bootstrap", () => {
  it("fills workItems and sets lastSeq to the cursor", async () => {
    vi.spyOn(await import("./api"), "listWorkItems").mockResolvedValue({
      items: [baseItem({ id: "wa" })],
      cursor: 42,
    });
    await useStore.getState().bootstrap();
    const s = useStore.getState();
    expect(s.workItems.wa).toBeDefined();
    expect(s.lastSeq).toBe(42);
  });
});
```

- [ ] **Step 2: Run — expect FAIL** (`Cannot find module './store'`).

- [ ] **Step 3: Implement `frontend/src/store.ts`**

```ts
import { create } from "zustand";
import * as api from "./api";
import type { KraftEvent, WorkItem, WorkerSession } from "./types";

type Connection = "connecting" | "open" | "reconnecting";

interface State {
  workItems: Record<string, WorkItem>;
  sessionsByItem: Record<string, WorkerSession[]>;
  eventsByItem: Record<string, KraftEvent[]>;
  lastSeq: number;
  connection: Connection;
  bootstrap: () => Promise<void>;
  hydrateItem: (id: string) => Promise<void>;
  applyEvent: (ev: KraftEvent) => void;
  setConnection: (c: Connection) => void;
}

function patchItem(
  s: State,
  id: string,
  fn: (w: WorkItem) => WorkItem,
): Partial<State> {
  const cur = s.workItems[id];
  if (!cur) return {};
  return { workItems: { ...s.workItems, [id]: fn(cur) } };
}

export const useStore = create<State>((set, get) => ({
  workItems: {},
  sessionsByItem: {},
  eventsByItem: {},
  lastSeq: 0,
  connection: "connecting",

  setConnection: (connection) => set({ connection }),

  bootstrap: async () => {
    const { items, cursor } = await api.listWorkItems();
    set({
      workItems: Object.fromEntries(items.map((i) => [i.id, i])),
      lastSeq: cursor,
    });
  },

  hydrateItem: async (id) => {
    const [full, evs] = await Promise.all([
      api.getWorkItem(id),
      api.getEvents(id),
    ]);
    const { worker_sessions, ...item } = full;
    set((s) => ({
      workItems: { ...s.workItems, [id]: { ...s.workItems[id], ...item } },
      sessionsByItem: { ...s.sessionsByItem, [id]: worker_sessions },
      eventsByItem: { ...s.eventsByItem, [id]: evs },
    }));
  },

  applyEvent: (ev) => {
    const id = ev.work_item_id;
    const p = ev.payload as Record<string, any>;
    set((s) => {
      const prevEvents = s.eventsByItem[id] ?? [];
      const base: Partial<State> = {
        lastSeq: Math.max(s.lastSeq, ev.seq),
        eventsByItem: { ...s.eventsByItem, [id]: [...prevEvents, ev] },
      };
      switch (ev.type) {
        case "work_item_created":
        case "chain_loaded":
          if (!s.workItems[id]) queueMicrotask(() => get().hydrateItem(id));
          return base;
        case "node_started":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, current_node_id: p.node_id })) };
        case "node_completed":
          return {
            ...base,
            ...patchItem(s, id, (w) => ({
              ...w,
              completedNodes: [...new Set([...(w.completedNodes ?? []), p.node_id])],
            })),
          };
        case "worker_session_started": {
          const rows = s.sessionsByItem[id] ?? [];
          const row: WorkerSession = {
            id: p.session_id,
            work_item_id: id,
            node_id: p.node_id,
            hook_point: p.hook_point,
            status: "running",
            attempt: 1,
            created_at: ev.created_at,
            exited_at: null,
          };
          return { ...base, sessionsByItem: { ...s.sessionsByItem, [id]: upsert(rows, row) } };
        }
        case "worker_session_exited":
        case "session_unknown": {
          const rows = s.sessionsByItem[id] ?? [];
          const status = ev.type === "session_unknown" ? "unknown" : p.status;
          return {
            ...base,
            sessionsByItem: {
              ...s.sessionsByItem,
              [id]: rows.map((r) => (r.id === p.session_id ? { ...r, status } : r)),
            },
          };
        }
        case "fix_cycle_started":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, fixCycle: p.cycle })) };
        case "gate_requested":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, pendingGate: p.gate })) };
        case "gate_approved":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, pendingGate: null, status: "active" })) };
        case "gate_rejected":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, pendingGate: null, rejectNote: p.note })) };
        case "work_item_needs_human":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, status: "needs_human" })) };
        case "work_item_completed":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, status: "completed" })) };
        default:
          return base;
      }
    });
  },
}));

function upsert(rows: WorkerSession[], row: WorkerSession): WorkerSession[] {
  const i = rows.findIndex((r) => r.id === row.id);
  if (i === -1) return [...rows, row];
  const copy = rows.slice();
  copy[i] = { ...copy[i], ...row };
  return copy;
}
```

- [ ] **Step 4: Run — expect PASS.** `cd frontend && npx vitest run src/store.test.ts`

Note: the "triggers hydrateItem" test spies on `get().hydrateItem`; the `queueMicrotask` indirection means the test must `await Promise.resolve()` — adjust the test to `await new Promise((r) => setTimeout(r))` if the spy assertion races. Keep the reducer's `queueMicrotask` (a store action must not be async).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store.ts frontend/src/store.test.ts
git commit -m "feat(frontend): normalized zustand store + event reducer"
```

---

## Task 4: WebSocket client with reconnect

**Files:**
- Create: `frontend/src/ws.ts`, `frontend/src/ws.test.ts`

**Interfaces:**
- Consumes: `useStore` (Task 3).
- Produces: `connectEvents(): () => void` — opens `WS /ws/events?after_seq=<lastSeq>`, wires `onmessage → store.applyEvent`, sets `connection`, and on close/error reconnects with backoff `[1000, 2000, 5000, 10000]` (capped) using the **current** `lastSeq`. Returns a disposer that stops reconnecting and closes the socket.

- [ ] **Step 1: Write the failing test — `frontend/src/ws.test.ts`**

```ts
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "./store";
import { connectEvents } from "./ws";

class FakeWS {
  static instances: FakeWS[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((e: { data: string }) => void) | null = null;
  close = vi.fn();
  constructor(url: string) {
    this.url = url;
    FakeWS.instances.push(this);
  }
}

beforeEach(() => {
  FakeWS.instances = [];
  vi.stubGlobal("WebSocket", FakeWS as unknown as typeof WebSocket);
  vi.useFakeTimers();
  useStore.setState({ lastSeq: 3, connection: "connecting" } as never);
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("connectEvents", () => {
  it("connects with the current lastSeq and marks the connection open", () => {
    connectEvents();
    expect(FakeWS.instances[0].url).toContain("after_seq=3");
    FakeWS.instances[0].onopen!();
    expect(useStore.getState().connection).toBe("open");
  });

  it("applies inbound frames to the store", () => {
    connectEvents();
    FakeWS.instances[0].onmessage!({
      data: JSON.stringify({ seq: 9, work_item_id: "w1", type: "x", payload: {}, created_at: "t" }),
    });
    expect(useStore.getState().lastSeq).toBe(9);
  });

  it("reconnects after close using the updated lastSeq and escalating backoff", () => {
    connectEvents();
    useStore.setState({ lastSeq: 20 } as never);
    FakeWS.instances[0].onclose!();
    expect(useStore.getState().connection).toBe("reconnecting");
    vi.advanceTimersByTime(1000);
    expect(FakeWS.instances[1].url).toContain("after_seq=20");
    // second failure -> longer wait
    FakeWS.instances[1].onclose!();
    vi.advanceTimersByTime(1999);
    expect(FakeWS.instances).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(FakeWS.instances).toHaveLength(3);
  });

  it("the disposer stops reconnection", () => {
    const stop = connectEvents();
    stop();
    FakeWS.instances[0].onclose!();
    vi.advanceTimersByTime(60000);
    expect(FakeWS.instances).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement `frontend/src/ws.ts`**

```ts
import { useStore } from "./store";

const BACKOFF = [1000, 2000, 5000, 10000];

export function connectEvents(): () => void {
  let attempt = 0;
  let stopped = false;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const open = () => {
    if (stopped) return;
    const seq = useStore.getState().lastSeq;
    socket = new WebSocket(
      `${location.origin.replace(/^http/, "ws")}/ws/events?after_seq=${seq}`,
    );
    socket.onopen = () => {
      attempt = 0;
      useStore.getState().setConnection("open");
    };
    socket.onmessage = (e) => {
      useStore.getState().applyEvent(JSON.parse(e.data));
    };
    const retry = () => {
      if (stopped) return;
      useStore.getState().setConnection("reconnecting");
      const wait = BACKOFF[Math.min(attempt, BACKOFF.length - 1)];
      attempt += 1;
      timer = setTimeout(open, wait);
    };
    socket.onclose = retry;
    socket.onerror = retry;
  };

  open();

  return () => {
    stopped = true;
    if (timer) clearTimeout(timer);
    socket?.close();
  };
}
```

- [ ] **Step 4: Run — expect PASS.** `cd frontend && npx vitest run src/ws.test.ts`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/ws.ts frontend/src/ws.test.ts
git commit -m "feat(frontend): WS client with backoff reconnect"
```

---

## Task 5: `ChainStrip` component

**Files:**
- Create: `frontend/src/components/ChainStrip.tsx`, `frontend/src/components/ChainStrip.test.tsx`

**Interfaces:**
- Consumes: `ChainDefinition`, `WorkItem` (`types`).
- Produces: `<ChainStrip item={WorkItem} size="sm" | "lg" />`. Renders one element per `chain_definition.nodes`, in order:
  - `data-node={node.id}`, class includes `current` when `node.id === item.current_node_id`, `done` when in `item.completedNodes`.
  - `⚑` marker element when `node.gate_after` is set (title = the gate name).
  - task-count badge (`node.tasks.length`) when `> 1`.
  - fix-cycle badge (`item.fixCycle`) on the node whose `fix_loop` is set, when `current` and `fixCycle` is set.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ChainStrip } from "./ChainStrip";
import type { WorkItem } from "../types";

const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "w1",
    current_node_id: "verify",
    completedNodes: ["env_setup"],
    chain_definition: {
      template_id: "t",
      nodes: [
        { id: "env_setup", tasks: ["a"], gate_after: null },
        { id: "verify", tasks: ["a", "b"], gate_after: null, fix_loop: "verify_fix_loop" },
        { id: "done", tasks: ["c"], gate_after: "human_review_approval" },
      ],
    },
  } as WorkItem & typeof over);

describe("ChainStrip", () => {
  it("marks current, done, gates, and the task-count badge", () => {
    render(<ChainStrip item={item()} size="sm" />);
    expect(screen.getByTestId("node-env_setup").className).toContain("done");
    expect(screen.getByTestId("node-verify").className).toContain("current");
    expect(screen.getByTestId("node-verify")).toHaveTextContent("2"); // task-count badge
    expect(screen.getByTitle("human_review_approval")).toBeInTheDocument(); // gate marker
  });

  it("shows the fix-cycle badge on the current fix_loop node", () => {
    render(<ChainStrip item={item({ fixCycle: 3 })} size="lg" />);
    expect(screen.getByTestId("node-verify")).toHaveTextContent("fix · 3");
  });
});
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement `ChainStrip.tsx`**

```tsx
import type { WorkItem } from "../types";

export function ChainStrip({
  item,
  size,
}: {
  item: WorkItem;
  size: "sm" | "lg";
}) {
  const done = new Set(item.completedNodes ?? []);
  return (
    <ol className={`chain-strip ${size}`}>
      {item.chain_definition.nodes.map((n) => {
        const current = n.id === item.current_node_id;
        const cls = [
          "chain-node",
          current && "current",
          done.has(n.id) && "done",
        ]
          .filter(Boolean)
          .join(" ");
        return (
          <li key={n.id} data-testid={`node-${n.id}`} data-node={n.id} className={cls}>
            <span className="label">{n.id}</span>
            {n.gate_after && (
              <span className="gate" title={n.gate_after}>
                ⚑
              </span>
            )}
            {n.tasks.length > 1 && <span className="count">{n.tasks.length}</span>}
            {current && n.fix_loop && item.fixCycle != null && (
              <span className="fix">fix · {item.fixCycle}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}
```

- [ ] **Step 4: Run — expect PASS.**

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChainStrip.tsx frontend/src/components/ChainStrip.test.tsx
git commit -m "feat(frontend): ChainStrip chain-graph component"
```

---

## Task 6: Board + IntakeModal

**Files:**
- Create: `frontend/src/components/IntakeModal.tsx`, `frontend/src/views/Board.tsx`, `frontend/src/views/Board.test.tsx`

**Interfaces:**
- Consumes: `useStore`, `api`, `ChainStrip`, `react-router-dom` `useNavigate`.
- Produces:
  - `<Board />` — reads `workItems` from the store; renders a `.board-card` per item (repo tag, title, status badge with `data-status`, `<ChainStrip size="sm">`, link `to={/work-items/${id}}`); client-side `<select>` filters for repo / status / chain_template; a "New Work Item" button toggling `<IntakeModal>`.
  - `<IntakeModal onClose={() => void} />` — fields `repo` (text), `title` (text), `chain_template` (`<select>` from `api.getTemplates()`, default `quick-task`); Submit → `api.createWorkItem` → on success `navigate(/work-items/${id})` + `onClose()`; on throw, show `.form-error` with the message and keep the modal open.

- [ ] **Step 1: Write the failing test — `Board.test.tsx`**

```tsx
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { useStore } from "../store";
import type { WorkItem } from "../types";
import { Board } from "./Board";

const wi = (over: Partial<WorkItem>): WorkItem =>
  ({
    id: over.id ?? "w1",
    title: over.title ?? "Item",
    repo: over.repo ?? "/repo-a",
    status: over.status ?? "active",
    chain_template: "quick-task",
    chain_definition: { template_id: "quick-task", nodes: [{ id: "n", tasks: ["a"], gate_after: null }] },
    current_node_id: "n",
    bead_id: "B",
    created_at: "t",
    updated_at: "t",
    ...over,
  }) as WorkItem;

beforeEach(() => {
  useStore.setState({
    workItems: {
      w1: wi({ id: "w1", repo: "/repo-a", status: "active" }),
      w2: wi({ id: "w2", repo: "/repo-b", status: "completed" }),
    },
  } as never);
  vi.restoreAllMocks();
});

const renderBoard = () =>
  render(
    <MemoryRouter>
      <Board />
    </MemoryRouter>,
  );

describe("Board", () => {
  it("renders a card per work item", () => {
    renderBoard();
    expect(screen.getAllByTestId("board-card")).toHaveLength(2);
  });

  it("filters by status", async () => {
    renderBoard();
    await userEvent.selectOptions(screen.getByLabelText("status"), "completed");
    const cards = screen.getAllByTestId("board-card");
    expect(cards).toHaveLength(1);
    expect(within(cards[0]).getByText("w2", { exact: false })).toBeTruthy();
  });

  it("opens the intake modal", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task" }, { id: "default" }]);
    renderBoard();
    await userEvent.click(screen.getByRole("button", { name: /new work item/i }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2:** Add `IntakeModal.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { IntakeModal } from "./IntakeModal";

describe("IntakeModal", () => {
  it("submits and shows an inline error on failure", async () => {
    vi.spyOn(api, "getTemplates").mockResolvedValue([{ id: "quick-task" }]);
    vi.spyOn(api, "createWorkItem").mockRejectedValue(new Error("repo path does not exist"));
    render(
      <MemoryRouter>
        <IntakeModal onClose={() => {}} />
      </MemoryRouter>,
    );
    await userEvent.type(screen.getByLabelText("repo"), "/nope");
    await userEvent.type(screen.getByLabelText("title"), "do a thing");
    await userEvent.click(screen.getByRole("button", { name: /create/i }));
    expect(await screen.findByText(/repo path does not exist/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run — expect FAIL.**

- [ ] **Step 4: Implement `IntakeModal.tsx`**

```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import * as api from "../api";

export function IntakeModal({ onClose }: { onClose: () => void }) {
  const nav = useNavigate();
  const [templates, setTemplates] = useState<string[]>(["quick-task"]);
  const [repo, setRepo] = useState("");
  const [title, setTitle] = useState("");
  const [tpl, setTpl] = useState("quick-task");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.getTemplates().then((ts) => setTemplates(ts.map((t) => t.id))).catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = tpl === "quick-task" ? { repo, title } : { repo, title, chain_template: tpl };
      const { id } = await api.createWorkItem(body);
      onClose();
      nav(`/work-items/${id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-label="New work item">
      <form className="modal" onSubmit={submit}>
        <label>repo<input aria-label="repo" value={repo} onChange={(e) => setRepo(e.target.value)} required /></label>
        <label>title<input aria-label="title" value={title} onChange={(e) => setTitle(e.target.value)} required /></label>
        <label>template
          <select aria-label="template" value={tpl} onChange={(e) => setTpl(e.target.value)}>
            {templates.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
        </label>
        {error && <p className="form-error">{error}</p>}
        <div className="modal-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy}>Create</button>
        </div>
      </form>
    </div>
  );
}
```

- [ ] **Step 5: Implement `Board.tsx`**

```tsx
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChainStrip } from "../components/ChainStrip";
import { IntakeModal } from "../components/IntakeModal";
import { useStore } from "../store";

const uniq = (xs: string[]) => [...new Set(xs)].sort();

export function Board() {
  const items = useStore((s) => Object.values(s.workItems));
  const [modal, setModal] = useState(false);
  const [repo, setRepo] = useState("");
  const [status, setStatus] = useState("");
  const [tpl, setTpl] = useState("");

  const shown = useMemo(
    () =>
      items.filter(
        (i) =>
          (!repo || i.repo === repo) &&
          (!status || i.status === status) &&
          (!tpl || i.chain_template === tpl),
      ),
    [items, repo, status, tpl],
  );

  return (
    <div className="board">
      <header className="board-toolbar">
        <select aria-label="repo" value={repo} onChange={(e) => setRepo(e.target.value)}>
          <option value="">all repos</option>
          {uniq(items.map((i) => i.repo)).map((r) => <option key={r}>{r}</option>)}
        </select>
        <select aria-label="status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">any status</option>
          {["active", "needs_human", "completed"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <select aria-label="template" value={tpl} onChange={(e) => setTpl(e.target.value)}>
          <option value="">any template</option>
          {uniq(items.map((i) => i.chain_template)).map((t) => <option key={t}>{t}</option>)}
        </select>
        <button onClick={() => setModal(true)}>New Work Item</button>
      </header>

      <ul className="board-list">
        {shown.map((i) => (
          <li key={i.id} data-testid="board-card" className="board-card">
            <Link to={`/work-items/${i.id}`}>
              <span className="repo-tag">{i.repo}</span>
              <span className="title">{i.title}</span>
              <span className="status" data-status={i.status}>{i.status}</span>
              <code className="wid">{i.id}</code>
            </Link>
            <ChainStrip item={i} size="sm" />
          </li>
        ))}
      </ul>

      {modal && <IntakeModal onClose={() => setModal(false)} />}
    </div>
  );
}
```

- [ ] **Step 6: Run — expect PASS.** `cd frontend && npx vitest run src/views/Board.test.tsx src/components/IntakeModal.test.tsx`

- [ ] **Step 7: Commit**

```bash
git add frontend/src/views/Board.tsx frontend/src/views/Board.test.tsx \
  frontend/src/components/IntakeModal.tsx frontend/src/components/IntakeModal.test.tsx
git commit -m "feat(frontend): Board view + New Work Item modal"
```

---

## Task 7: Work Item Detail + sub-components

**Files:**
- Create: `frontend/src/components/CurrentNodePanel.tsx`, `frontend/src/components/EventTimeline.tsx`, `frontend/src/components/LogModal.tsx`, `frontend/src/components/Gate.tsx`, `frontend/src/views/WorkItemDetail.tsx`, and co-located `*.test.tsx` for `CurrentNodePanel`, `Gate`, `WorkItemDetail`.

**Interfaces:**
- Consumes: `useStore` (`hydrateItem`, `workItems`, `sessionsByItem`, `eventsByItem`), `api` (`approveGate`, `rejectGate`, `logUrl`), `ChainStrip`, `useParams`.
- Produces:
  - `<Gate item gate />` — `Approve` button → `api.approveGate(item.id, gate)`; `Reject` reveals a `<textarea aria-label="reject note">`, submit disabled until non-empty → `api.rejectGate(item.id, gate, note)`.
  - `<CurrentNodePanel item sessions />` — one `.session-row` per session at `item.current_node_id`; status chip `data-status={s.status}`; fix rows (hook `on.implementation.start` + `item.fixCycle`) badged `fix · cycle N`. When every current-node session status is in `{done}` and the node has `gate_after` and there is no session for the next node → render `<Gate>` (awaiting-gate inference, spec §3.5).
  - `<EventTimeline events />` — `<ol>` newest-first; a `worker_session_*` event row has a "view log" button → opens `<LogModal sessionId={payload.session_id} />`.
  - `<LogModal sessionId onClose />` — `fetch(api.logUrl(sessionId))` → `res.text()` into a `<pre>`.
  - `<WorkItemDetail />` — `useParams` id; `useEffect` → `store.hydrateItem(id)` + `setInterval(() => hydrateItem(id), 60000)` cleared on unmount; renders `<ChainStrip size="lg">`, `<CurrentNodePanel>`, `<EventTimeline>`. `404` guard: if `hydrateItem` throws, show "unknown work item".

- [ ] **Step 1: Write failing tests**

`Gate.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import * as api from "../api";
import { Gate } from "./Gate";

const item = { id: "w1" } as never;

describe("Gate", () => {
  it("approve calls the API", async () => {
    const spy = vi.spyOn(api, "approveGate").mockResolvedValue();
    render(<Gate item={item} gate="spec_approval" />);
    await userEvent.click(screen.getByRole("button", { name: /approve/i }));
    expect(spy).toHaveBeenCalledWith("w1", "spec_approval");
  });

  it("reject submit is disabled until a note is entered", async () => {
    const spy = vi.spyOn(api, "rejectGate").mockResolvedValue();
    render(<Gate item={item} gate="spec_approval" />);
    await userEvent.click(screen.getByRole("button", { name: /^reject/i }));
    const submit = screen.getByRole("button", { name: /submit rejection/i });
    expect(submit).toBeDisabled();
    await userEvent.type(screen.getByLabelText("reject note"), "redo the spec");
    expect(submit).toBeEnabled();
    await userEvent.click(submit);
    expect(spy).toHaveBeenCalledWith("w1", "spec_approval", "redo the spec");
  });
});
```

`CurrentNodePanel.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { WorkItem, WorkerSession } from "../types";
import { CurrentNodePanel } from "./CurrentNodePanel";

const item = {
  id: "w1",
  current_node_id: "verify",
  fixCycle: 2,
  chain_definition: {
    template_id: "t",
    nodes: [
      { id: "verify", tasks: ["on.test.run"], gate_after: null, fix_loop: "verify_fix_loop" },
    ],
  },
} as WorkItem;

const s = (over: Partial<WorkerSession>): WorkerSession =>
  ({
    id: "s1", work_item_id: "w1", node_id: "verify", hook_point: "on.test.run",
    status: "running", attempt: 1, created_at: "t", exited_at: null, ...over,
  }) as WorkerSession;

describe("CurrentNodePanel", () => {
  it("renders a chip per current-node session", () => {
    render(<CurrentNodePanel item={item} sessions={[s({ id: "a", status: "running" }), s({ id: "b", status: "capped_out" })]} />);
    expect(screen.getByTestId("session-a")).toHaveAttribute("data-status", "running");
    expect(screen.getByTestId("session-b")).toHaveAttribute("data-status", "capped_out");
  });

  it("badges a fix task row", () => {
    render(<CurrentNodePanel item={item} sessions={[s({ id: "f", hook_point: "on.implementation.start" })]} />);
    expect(screen.getByTestId("session-f")).toHaveTextContent("fix · cycle 2");
  });
});
```

`WorkItemDetail.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useStore } from "../store";
import { WorkItemDetail } from "./WorkItemDetail";

beforeEach(() => {
  useStore.setState({
    workItems: {
      w1: {
        id: "w1", title: "T", repo: "/r", status: "active", chain_template: "quick-task",
        chain_definition: { template_id: "quick-task", nodes: [{ id: "n", tasks: ["a"], gate_after: null }] },
        current_node_id: "n", bead_id: "B", created_at: "t", updated_at: "t",
      },
    },
    sessionsByItem: { w1: [] },
    eventsByItem: { w1: [] },
  } as never);
  vi.spyOn(useStore.getState(), "hydrateItem").mockResolvedValue(undefined);
});

describe("WorkItemDetail", () => {
  it("hydrates on mount and renders the stepper", async () => {
    render(
      <MemoryRouter initialEntries={["/work-items/w1"]}>
        <Routes>
          <Route path="/work-items/:id" element={<WorkItemDetail />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(useStore.getState().hydrateItem).toHaveBeenCalledWith("w1");
    expect(screen.getByTestId("node-n")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement `Gate.tsx`**

```tsx
import { useState } from "react";
import * as api from "../api";
import type { WorkItem } from "../types";

export function Gate({ item, gate }: { item: WorkItem; gate: string }) {
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  return (
    <div className="gate" data-gate={gate}>
      <span className="gate-label">⚑ {gate}</span>
      {!rejecting && (
        <>
          <button disabled={busy} onClick={async () => { setBusy(true); await api.approveGate(item.id, gate); }}>
            Approve
          </button>
          <button disabled={busy} onClick={() => setRejecting(true)}>Reject…</button>
        </>
      )}
      {rejecting && (
        <div className="gate-reject">
          <textarea aria-label="reject note" value={note} onChange={(e) => setNote(e.target.value)} />
          <button
            disabled={busy || note.trim() === ""}
            onClick={async () => { setBusy(true); await api.rejectGate(item.id, gate, note); }}
          >
            Submit rejection
          </button>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Implement `CurrentNodePanel.tsx`**

```tsx
import { useState } from "react";
import type { WorkItem, WorkerSession } from "../types";
import { Gate } from "./Gate";

export function CurrentNodePanel({
  item,
  sessions,
}: {
  item: WorkItem;
  sessions: WorkerSession[];
}) {
  const node = item.chain_definition.nodes.find((n) => n.id === item.current_node_id);
  const rows = sessions.filter((s) => s.node_id === item.current_node_id);
  const nextNode = item.chain_definition.nodes[
    item.chain_definition.nodes.findIndex((n) => n.id === item.current_node_id) + 1
  ];
  const awaitingGate =
    !!node?.gate_after &&
    rows.length > 0 &&
    rows.every((s) => s.status === "done") &&
    !sessions.some((s) => nextNode && s.node_id === nextNode.id);

  const [logSid, setLogSid] = useState<string | null>(null);

  return (
    <section className="current-node">
      <h3>{item.current_node_id}</h3>
      <ul>
        {rows.map((s) => (
          <li key={s.id} data-testid={`session-${s.id}`} className="session-row" data-status={s.status}>
            <span className="hook">{s.hook_point}</span>
            <span className="chip" data-status={s.status}>{s.status}</span>
            {s.hook_point === "on.implementation.start" && item.fixCycle != null && (
              <span className="fix-badge">fix · cycle {item.fixCycle}</span>
            )}
            <button onClick={() => setLogSid(s.id)}>view log</button>
          </li>
        ))}
      </ul>
      {awaitingGate && node?.gate_after && <Gate item={item} gate={node.gate_after} />}
      {logSid && <LogModal sessionId={logSid} onClose={() => setLogSid(null)} />}
    </section>
  );
}

import { LogModal } from "./LogModal";
```

Move the `import { LogModal }` to the top with the other imports (shown inline only for locality).

- [ ] **Step 5: Implement `LogModal.tsx`**

```tsx
import { useEffect, useState } from "react";
import * as api from "../api";

export function LogModal({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [text, setText] = useState("loading…");
  useEffect(() => {
    let live = true;
    fetch(api.logUrl(sessionId))
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`log ${r.status}`))))
      .then((t) => live && setText(t))
      .catch((e) => live && setText(String(e)));
    return () => {
      live = false;
    };
  }, [sessionId]);
  return (
    <div className="modal-backdrop" role="dialog" aria-label="session log">
      <div className="modal log-modal">
        <button onClick={onClose}>close</button>
        <pre>{text}</pre>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Implement `EventTimeline.tsx`**

```tsx
import { useState } from "react";
import type { KraftEvent } from "../types";
import { LogModal } from "./LogModal";

export function EventTimeline({ events }: { events: KraftEvent[] }) {
  const [sid, setSid] = useState<string | null>(null);
  return (
    <section className="timeline">
      <ol>
        {[...events].reverse().map((e) => (
          <li key={e.seq} className="event-row" data-type={e.type}>
            <time>{e.created_at}</time>
            <span className="etype">{e.type}</span>
            {e.type.startsWith("worker_session_") && typeof e.payload.session_id === "string" && (
              <button onClick={() => setSid(e.payload.session_id as string)}>view log</button>
            )}
          </li>
        ))}
      </ol>
      {sid && <LogModal sessionId={sid} onClose={() => setSid(null)} />}
    </section>
  );
}
```

- [ ] **Step 7: Implement `WorkItemDetail.tsx`**

```tsx
import { useEffect } from "react";
import { Link, useParams } from "react-router-dom";
import { ChainStrip } from "../components/ChainStrip";
import { CurrentNodePanel } from "../components/CurrentNodePanel";
import { EventTimeline } from "../components/EventTimeline";
import { useStore } from "../store";

export function WorkItemDetail() {
  const { id = "" } = useParams();
  const item = useStore((s) => s.workItems[id]);
  const sessions = useStore((s) => s.sessionsByItem[id] ?? []);
  const events = useStore((s) => s.eventsByItem[id] ?? []);
  const hydrateItem = useStore((s) => s.hydrateItem);

  useEffect(() => {
    hydrateItem(id).catch(() => {});
    const t = setInterval(() => hydrateItem(id).catch(() => {}), 60_000);
    return () => clearInterval(t);
  }, [id, hydrateItem]);

  if (!item) return <p className="empty">unknown work item</p>;

  return (
    <div className="detail">
      <Link to="/">← board</Link>
      <h2>{item.title}</h2>
      <ChainStrip item={item} size="lg" />
      <CurrentNodePanel item={item} sessions={sessions} />
      <EventTimeline events={events} />
    </div>
  );
}
```

- [ ] **Step 8: Run — expect PASS.** `cd frontend && npx vitest run src/components src/views/WorkItemDetail.test.tsx`

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components frontend/src/views/WorkItemDetail.tsx frontend/src/views/WorkItemDetail.test.tsx
git commit -m "feat(frontend): Work Item Detail — stepper, current-node panel, timeline, gates, log modal"
```

---

## Task 8: App shell, badges, boot wiring

**Files:**
- Create: `frontend/src/App.tsx`, `frontend/src/components/ConnBadge.tsx`, `frontend/src/components/HealthBadge.tsx`, `frontend/src/App.test.tsx`
- Modify: `frontend/src/main.tsx` (replace the stub), `frontend/src/styles.css` (real styles), delete `frontend/src/smoke.test.ts`

**Interfaces:**
- Consumes: `useStore`, `api.getHealth`, `connectEvents`, `react-router-dom`.
- Produces:
  - `<ConnBadge />` — `useStore(s => s.connection)`; renders nothing when `open`, else `.conn-badge` with "reconnecting…" / "connecting…".
  - `<HealthBadge />` — polls `api.getHealth()` on mount + every 60s; when `status === "degraded"`, renders `.health-badge` listing `invalid_templates` + `invalid_policy`.
  - `<App />` — `<BrowserRouter>` with routes `/` → `<Board>`, `/work-items/:id` → `<WorkItemDetail>`; header contains `<ConnBadge>` + `<HealthBadge>`.
  - `main.tsx` — `await store.bootstrap()` then `connectEvents()` then render `<App>`. Bootstrap failure still renders `<App>` (empty board) so the UI is not a blank screen.

- [ ] **Step 1: Write the failing test — `App.test.tsx`**

```tsx
import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { useStore } from "./store";
import { App } from "./App";

beforeEach(() => {
  useStore.setState({ workItems: {}, connection: "reconnecting" } as never);
  vi.spyOn(api, "getHealth").mockResolvedValue({ status: "degraded", invalid_templates: ["broken"], invalid_policy: [] });
});

describe("App", () => {
  it("shows the reconnecting badge and a degraded-health flag", async () => {
    render(<App />);
    expect(screen.getByText(/reconnecting/i)).toBeInTheDocument();
    expect(await screen.findByText(/broken/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3: Implement `ConnBadge.tsx`**

```tsx
import { useStore } from "../store";

export function ConnBadge() {
  const c = useStore((s) => s.connection);
  if (c === "open") return null;
  return <span className="conn-badge">{c === "connecting" ? "connecting…" : "reconnecting…"}</span>;
}
```

- [ ] **Step 4: Implement `HealthBadge.tsx`**

```tsx
import { useEffect, useState } from "react";
import * as api from "../api";
import type { Health } from "../types";

export function HealthBadge() {
  const [h, setH] = useState<Health | null>(null);
  useEffect(() => {
    const load = () => api.getHealth().then(setH).catch(() => {});
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);
  if (!h || h.status !== "degraded") return null;
  return (
    <span className="health-badge">
      degraded: {[...h.invalid_templates, ...h.invalid_policy].join(", ")}
    </span>
  );
}
```

- [ ] **Step 5: Implement `App.tsx`**

```tsx
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { ConnBadge } from "./components/ConnBadge";
import { HealthBadge } from "./components/HealthBadge";
import { Board } from "./views/Board";
import { WorkItemDetail } from "./views/WorkItemDetail";

export function App() {
  return (
    <BrowserRouter>
      <header className="app-header">
        <span className="brand">Kraft</span>
        <ConnBadge />
        <HealthBadge />
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Board />} />
          <Route path="/work-items/:id" element={<WorkItemDetail />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}
```

- [ ] **Step 6: Replace `main.tsx`**

```tsx
import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { useStore } from "./store";
import "./styles.css";
import { connectEvents } from "./ws";

async function boot() {
  try {
    await useStore.getState().bootstrap();
  } catch (e) {
    console.error("bootstrap failed", e);
  }
  connectEvents();
  createRoot(document.getElementById("root")!).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void boot();
```

- [ ] **Step 7:** `git rm frontend/src/smoke.test.ts`. Write real `styles.css` (plain CSS: header bar, `.board-list` grid, `.chain-strip` flex row with `.current`/`.done` colouring, `.chip[data-status]` colours for each `SessionStatus`, `.modal-backdrop` overlay, `.health-badge`/`.conn-badge` pill). Keep it under ~200 lines; it is not tested.

- [ ] **Step 8: Run the full suite + build**

Run:
```bash
cd frontend && npm test && npm run build
```
Expected: all Vitest specs pass; `tsc -b` clean (strict); `dist/` produced.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/main.tsx \
  frontend/src/components/ConnBadge.tsx frontend/src/components/HealthBadge.tsx \
  frontend/src/styles.css
git rm frontend/src/smoke.test.ts
git commit -m "feat(frontend): app shell, router, connection + health badges, boot sequence"
```

---

## Task 9: Playwright end-to-end

**Files:**
- Create: `frontend/playwright.config.ts`, `frontend/e2e/chain.spec.ts`, `frontend/e2e/fixtures.ts`

**Interfaces:**
- Consumes: the built `dist/`, a running orchestrator with the fake agent, `tests/support` fixtures (invoked via a small Python helper or shell).
- Produces: one spec — open Board, create a `quick-task` work item against the sample repo, assert the card's status badge reaches `completed` and the chain strip shows all nodes `done`.

- [ ] **Step 1: Create `frontend/playwright.config.ts`**

```ts
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  use: { baseURL: process.env.KRAFT_E2E_BASE ?? "http://127.0.0.1:8765" },
  reporter: "list",
});
```

- [ ] **Step 2: Create `frontend/e2e/chain.spec.ts`**

```ts
import { expect, test } from "@playwright/test";

// Assumes an orchestrator is already running at baseURL with:
//   - KRAFT_FRONTEND_DIST pointed at ../dist
//   - the fake agent wired (KRAFT_FAKE_AGENT=fix or the fake-claude.sh registry)
//   - KRAFT_E2E_REPO set to a git repo path with a failing test
const REPO = process.env.KRAFT_E2E_REPO!;

test("create a work item and watch it complete", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /new work item/i }).click();
  await page.getByLabel("repo").fill(REPO);
  await page.getByLabel("title").fill("make the failing test pass");
  await page.getByRole("button", { name: /create/i }).click();

  // navigated to detail; wait for terminal state
  await expect(page.locator(".detail h2")).toHaveText("make the failing test pass");
  await expect(page.locator('[data-status="completed"]')).toBeVisible({ timeout: 100_000 });
});
```

- [ ] **Step 3: Document the runner** — add `frontend/e2e/README.md` with the exact commands to (a) build `dist/`, (b) start the orchestrator with `KRAFT_FRONTEND_DIST`, fake agent env, and an `isolated_bd` tracker (reuse `tests/support/server.py` patterns or a one-off `python -m kraft` invocation), (c) `KRAFT_E2E_REPO=<repo> npx playwright test`. This mirrors how `tests/test_e2e.py` is gated today (`KRAFT_E2E=1`).

- [ ] **Step 4: Wire CI (manual/nightly only)** — in `.gitlab-ci.yml`, add to the `frontend` job's `script` **nothing** for e2e (browsers + a live server are out of scope for the blocking gate). Add a separate `frontend-e2e` job with `when: manual` that installs Playwright browsers, builds, starts the server, and runs `npm run e2e`. Keep it non-blocking, consistent with the Python `-m e2e` treatment.

- [ ] **Step 5: Run locally once** to prove the flow, following `e2e/README.md`. Expected: the spec passes; the work item reaches `completed` in the UI.

- [ ] **Step 6: Commit**

```bash
git add frontend/playwright.config.ts frontend/e2e .gitlab-ci.yml
git commit -m "test(frontend): Playwright e2e — create work item, reach completed"
```

---

## Task 10: Close out 3B

- [ ] **Step 1:** From `frontend/`: `npm test && npm run build`. From repo root: `uv run pytest -q -m "not e2e"` (3A's `test_api.py` SPA test now has a real `dist/` if you point `KRAFT_FRONTEND_DIST` at it — but the test builds its own fake dist, so this is just a regression check). All green.
- [ ] **Step 2:** `uv run ruff check . && uv run ruff format --check .` — unaffected, confirm clean.
- [ ] **Step 3:** `bd close Kraft-kif --reason="3A + 3B landed: WS transport backend + React SPA (Board, intake, detail, gates). Deferred items (auth, pause/steer, search, docs, federation panels) tracked for their efforts."`
- [ ] **Step 4:** Update `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md` §3: mark effort #3 done; note the deferred UI slices.
- [ ] **Step 5:** Report handoff: new `frontend/` tree, the `frontend` CI job, test counts (Vitest + backend), and the deferred list.

---

## Self-Review

**Spec coverage (§3, §4, §5, §6.3–6.5, §7 chunk 3B):**
- §3.1 layout/tooling (Vite, React, TS strict, plain CSS, dev proxy) → Task 1. ✓
- §3.2 store shape + `applyEvent` reducer table (every row) → Task 3 (one test per row). ✓
- §3.3 WS client (connect w/ `lastSeq`, `onmessage`, backoff `[1,2,5,10]s`, disposer) → Task 4. ✓
- §3.4 API client (all 9 functions, `detail` error extraction) → Task 2. ✓
- §3.5 views: Board (cards, `<ChainStrip>`, filters, New Work Item) → Task 6; IntakeModal (fields, navigate, inline error) → Task 6; WorkItemDetail (hydrate on mount + 60s, stepper, current-node panel, timeline) → Task 7; CurrentNodePanel (chips per `SessionStatus`, fix badge, awaiting-gate inference) → Task 7; EventTimeline + LogModal (pull-based `<pre>`) → Task 7; Gate (approve / reject+required note) → Task 7; ChainStrip → Task 5. ✓
- §3.5 ConnBadge / HealthBadge → Task 8. ✓
- §3.6 routing (`react-router-dom`, 2 routes) → Task 8. ✓
- §4 data flow (bootstrap → cursor → WS `after_seq`; detail hydrate + 60s) → Tasks 3 + 7 + 8. ✓
- §5 error handling: API 4xx inline (Task 6 IntakeModal test, Task 2 test); WS drop → reconnecting badge (Task 4 + 8); 60s reconcile (Task 7); bootstrap failure still renders (Task 8 `main.tsx`). ✓
- §6.3 Vitest/RTL specs → every component/store/ws task. §6.4 one Playwright spec → Task 9. §6.5 `.gitlab-ci.yml` `frontend` job → Task 1; e2e manual job → Task 9. ✓
- §7 chunk 3B "done when": `npm run build` → `dist/`, Vitest green, e2e drives `quick-task` to `completed` → Tasks 8 + 9. ✓

**Placeholder scan:** all component/store/ws/api steps carry literal code. `styles.css` (Task 8 Step 7) and `e2e/README.md` (Task 9 Step 3) are described, not code-blocked — acceptable: CSS is untested cosmetic detail (spec §3.1, §10 defer iconography) and the README is prose by nature. No "TBD"/"handle errors"/"similar to Task N".

**Type consistency:** `KraftEvent`/`WorkItem`/`WorkerSession`/`SessionStatus`/`Health` defined once in Task 2, imported everywhere. Store actions `bootstrap`/`hydrateItem`/`applyEvent`/`setConnection` — same names Task 3 → Tasks 4, 6, 7, 8. `connectEvents(): () => void` — Task 4 def matches Task 8 `main.tsx` call. `api.*` names Task 2 ↔ every consumer. `<ChainStrip item size>` — Task 5 def matches Board (Task 6) + Detail (Task 7). `<Gate item gate>` — Task 7 def matches CurrentNodePanel usage. `data-status` attribute used consistently on board card status + session chips. `after_seq` query param name matches 3A Task 3/4. ✓
