# WebSocket Transport + React UI (Buildable-Now Slice) — Design

**Status:** design, approved in chat, awaiting written-spec review
**Date:** 2026-09-03
**Bead:** Kraft-kif
**Consolidated design refs:** `docs/consolidated/05_ui.md` (all); `docs/consolidated/02_orchestrator_core.md` §2, §5, §6, §9, §10.1
**Master plan:** post-skeleton effort #3 of `docs/superpowers/plans/2026-09-01-kraft-walking-skeleton-master-plan.md` §3

---

## 0. What this is

The skeleton + efforts #1/#2 give a running orchestrator with a REST API
(`POST /work-items`, `GET /work-items/{id}`, `GET /work-items/{id}/events`, gate
approve/reject, `GET /worker-sessions/{id}/log`, `GET /health`) and an append-only
`events` table. There is no live push transport and no UI — a human watching a run
polls `GET .../events?after_seq=` by hand.

`05_ui.md` designs the full local SPA, but it consumes API surface from efforts
still unbuilt: search / documents / index (#4), GitLab MR + `involved_repos` /
`work_item_repos` (#5, #6), pause/steer (`02` §10.2), and `/login` auth. This
effort builds the slice that the **current** API can support, plus the two small
endpoints and the transport the UI needs:

1. `Database.on_commit` — a post-commit hook.
2. `kraft.ws` — an in-process event broadcaster fed by `on_commit`.
3. `WS /ws/events` — one global connection, `after_seq` catch-up + live tail.
4. `GET /work-items` — the list endpoint (with an event `cursor`).
5. `GET /templates` — resolvable template ids, for the intake modal.
6. Static serving of a built SPA from the FastAPI process.
7. A React/Vite/TypeScript SPA: normalized Zustand store, WS sync with
   reconnect, Board, New Work Item modal, Work Item Detail (chain stepper +
   current-node panel + event timeline + log modal), inline Gates.

It does **not** add auth / `/login`, pause / steer / resume, the repos panel /
repo cluster / cross-repo intake disclosure, the search overlay, the Document
Viewer, the system-status reindex control, or a mobile layout. Those are their
own efforts. See §9.

---

## 1. Scope

### In

- `src/kraft/db.py` — `Database.__init__` gains `on_commit: Callable[[], None] | None`;
  `_run` fires it after a successful commit.
- `src/kraft/ws.py` (new) — `Broadcaster`: per-client bounded queues, a cursor,
  `notify()`, and a background fan-out task.
- `src/kraft/api.py` — wire `Broadcaster` into `lifespan` + `Database.on_commit`;
  add `WS /ws/events`, `GET /work-items`, `GET /templates`; mount static assets +
  SPA catch-all when `frontend/dist` exists.
- `frontend/` (new) — Vite + React + TypeScript SPA. Zustand store, WS/API
  clients, Board / IntakeModal / WorkItemDetail / ChainStrip / ChainStepper /
  CurrentNodePanel / EventTimeline / LogModal / Gate / ConnBadge / HealthBadge.
- `.gitlab-ci.yml` — a `frontend` job (`npm ci && npm run build && npm test`).
- `.gitignore` — `frontend/node_modules`, `frontend/dist`.
- Tests: `tests/test_ws.py` (new), `tests/test_api.py` (extend); `frontend/`
  Vitest + React Testing Library; one Playwright e2e.

### Out (later efforts — see §9)

- Auth / `/login` / `/logout` — this effort binds `127.0.0.1` only, auth
  middleware absent (`02` §9 common case). The SPA boot calls `GET /health`
  and, on 200, goes straight to the Board; no 401 path.
- `POST /pause` / `POST /steer` / `POST /resume` and the pause/steer controls
  (`02` §10.2 unbuilt).
- Repos panel, repo cluster, `involved_repo` filter, "Advanced / cross-repo"
  intake disclosure (`submodules`, `root_merge_policy`) — effort #6.
- Search overlay, bead-scoped search box, Document Viewer, `GET /search`,
  `GET /beads/search`, `GET /documents/{id}`, `GET /work-items/{id}/documents`
  — effort #4.
- System-status manual reindex control (`POST /index/rescan`) — effort #4.
- Mobile / phone-condensed layout.
- Per-view WS subscriptions — `05` §2 already rejects them; one global
  connection.

---

## 2. Backend

### 2.1 `Database.on_commit`

`__init__(self, writer, reader, *, on_commit=None)`. In `_run`, the success
branch becomes:

```
else:
    if not fut.done():
        fut.set_result(result)
    if self._on_commit is not None:
        try:
            self._on_commit()
        except Exception:
            logger.exception("on_commit listener raised")
```

- Fired **after** `fut.set_result` so the writer loop stays responsive and a slow
  listener never delays the caller's await.
- Guarded: a raising listener is logged and swallowed — it must not wedge the
  writer task (same reasoning as the rollback-ordering comment already in `_run`).
- Fired for every committed write, including no-op / SELECT-only writes. Harmless:
  the broadcaster reads the delta from `cursor` and finds nothing new.
- `on_commit` is sync and must not block. The broadcaster's `notify()` only sets
  an `asyncio.Event` (via `loop.call_soon_threadsafe` is **not** needed — `_run`
  is on the event loop already; a plain `Event.set()` is fine).

### 2.2 `kraft.ws.Broadcaster`

```
class Broadcaster:
    def __init__(self, db: Database) -> None
    async def start(self) -> None      # spawn the fan-out task
    async def stop(self) -> None
    def notify(self) -> None           # wake the fan-out task (from on_commit)
    def register(self) -> Client       # new per-connection queue; returns handle
    def unregister(self, client) -> None
    @property
    def cursor(self) -> int            # last seq fanned out
```

- State: `_clients: set[Client]`, `_cursor: int` (initialised to the current
  `MAX(seq)` at `start()`), `_wakeup: asyncio.Event`.
- `Client` wraps an `asyncio.Queue(maxsize=1000)` and a `dropped: bool` flag.
- Fan-out task loop: `await self._wakeup.wait()`; `self._wakeup.clear()`; read
  `events.read_after(self._cursor)` via `db.read`; for each event, for each
  client, `queue.put_nowait(event)` — on `QueueFull` set `client.dropped = True`
  and stop feeding it; advance `_cursor` to the last event's seq.
- A dropped client's WS handler notices `dropped` on its next loop turn, closes
  the socket with a "resubscribe" close code; the browser reconnects and catches
  up from its own tracked `after_seq`.
- `notify()` is called synchronously from `on_commit`; it only does
  `self._wakeup.set()`. Coalescing is automatic — many commits between fan-out
  turns produce one drain.

### 2.3 `WS /ws/events`

Query param `after_seq: int = 0`.

Connect sequence (order matters — no gap, no dup):

1. `client = broadcaster.register()` — start buffering live events immediately.
2. `live_start = broadcaster.cursor` — snapshot.
3. Catch-up: `for ev in db.read(events.read_after, after_seq)` where
   `ev["seq"] <= live_start`: send to socket.
4. Live: loop `ev = await client.queue.get()`; skip `ev["seq"] <= live_start`
   (already sent in step 3); send to socket. Break if `client.dropped`.
5. `finally: broadcaster.unregister(client)`.

Frame format: the event dict as JSON, exactly the `events.read_after` shape
(`seq`, `work_item_id`, `type`, `payload`, `created_at`).

**Origin check:** reject the handshake (close before accept) if the `Origin`
header is present and its host is not `localhost` / `127.0.0.1` / `[::1]`. Blunts
DNS-rebinding from a browser on another site. A non-browser client (no `Origin`)
is allowed.

### 2.4 `GET /work-items`

```
{
  "items": [
    { "id", "title", "repo", "status", "chain_template",
      "chain_definition": {...}, "current_node_id",
      "bead_id", "created_at", "updated_at" }
  ],
  "cursor": <int>   # MAX(seq) from events, 0 if none
}
```

- One `SELECT * FROM work_items ORDER BY created_at`.
- `chain_definition` parsed to JSON (same as `GET /work-items/{id}`).
- No `worker_sessions` — the Board renders only the chain strip + status badge;
  session detail is a `GET /work-items/{id}` concern.
- `cursor` is the seq the SPA hands to its first `WS /ws/events` connect, so the
  first connection does **no** historical catch-up (the list already gave current
  state). Reconnects use the client's own tracked `lastSeq`.

### 2.5 `GET /templates`

```
[ { "id": "quick-task" }, { "id": "default" } ]
```

`sorted(st.templates.valid)` — resolvable templates only, so a broken template
can't be picked in the intake modal (`05` §4.1a).

### 2.6 Static serving

- `frontend/dist/` is the Vite build output (git-ignored, built in CI and by the
  dev workflow). Path resolves to `<repo root>/frontend/dist`, overridable by
  `KRAFT_FRONTEND_DIST` (mirrors the existing `KRAFT_TEMPLATES_DIR` /
  `KRAFT_RUN_DIR` pattern; lets tests point at a tmp dir).
- If it exists at `lifespan` startup:
  - `app.mount("/assets", StaticFiles(directory=dist/"assets"))`.
  - A catch-all `GET /{path:path}` that returns `dist/index.html` for anything not
    matching an API route or `/assets` (SPA client routing: `/`,
    `/work-items/:id`). API routes are declared before the catch-all so they win.
- If it does not exist (tests, API-only dev): skip both. The API is fully usable
  without a build.

---

## 3. Frontend

### 3.1 Layout & tooling

```
frontend/
  package.json         vite, react, react-dom, react-router-dom, zustand,
                       typescript, vitest, @testing-library/react, jsdom,
                       @playwright/test
  vite.config.ts       base '/', build.outDir 'dist', test (vitest) config
  tsconfig.json
  index.html
  src/
    main.tsx           router + <App/>
    store.ts           Zustand store + applyEvent reducer
    ws.ts              WS client with reconnect
    api.ts             fetch wrappers
    types.ts           WorkItem, WorkerSession, KraftEvent, ChainNode
    views/Board.tsx
    views/WorkItemDetail.tsx
    components/IntakeModal.tsx
    components/ChainStrip.tsx      (board card + detail stepper, size prop)
    components/CurrentNodePanel.tsx
    components/EventTimeline.tsx
    components/LogModal.tsx
    components/Gate.tsx
    components/ConnBadge.tsx
    components/HealthBadge.tsx
    styles.css
  tests/                *.test.ts(x)
  e2e/                  playwright spec
```

- Vite dev server is dev-time only; the shipped app is `dist/` served by FastAPI
  (`05` §1).
- Plain CSS in `styles.css` + co-located class names. No component library.
- Dev proxy: `vite.config.ts` proxies `/work-items`, `/templates`, `/health`,
  `/worker-sessions`, `/ws` to `http://127.0.0.1:8000` so `npm run dev` talks to a
  locally-running orchestrator.

### 3.2 Store (`store.ts`, Zustand)

```
type State = {
  workItems: Record<string, WorkItem>
  sessionsByItem: Record<string, WorkerSession[]>
  eventsByItem: Record<string, KraftEvent[]>   // chronological
  lastSeq: number
  connection: 'connecting' | 'open' | 'reconnecting'
  // actions
  bootstrap(): Promise<void>
  hydrateItem(id: string): Promise<void>
  applyEvent(ev: KraftEvent): void
  setConnection(c): void
}
```

- `bootstrap()` — `GET /work-items` → fill `workItems`, set `lastSeq = cursor`.
- `hydrateItem(id)` — `GET /work-items/{id}` → replace that item + its
  `sessionsByItem[id]` from the authoritative response; also `GET
  /work-items/{id}/events` to seed `eventsByItem[id]`. Called on Detail mount and
  by the 60s reconciliation timer.
- `applyEvent(ev)` — always `lastSeq = max(lastSeq, ev.seq)` and push into
  `eventsByItem[ev.work_item_id]`. Then reduce by `ev.type`:

  | type | reduction |
  |---|---|
  | `work_item_created` | if unknown id → `hydrateItem(id)` (payload lacks `chain_definition`) |
  | `chain_loaded` | if unknown id → `hydrateItem(id)`; else no-op |
  | `node_started` | `workItems[id].current_node_id = payload.node_id` |
  | `node_completed` | mark node done in a local `completedNodes` set on the item |
  | `worker_session_started` | upsert `sessionsByItem[id]` row (`status:'running'`, from payload) |
  | `worker_session_exited` | set that session's `status` from `payload.status` |
  | `session_unknown` | set that session's `status = 'unknown'` |
  | `session_reattached` | no store change (badge only, from timeline) |
  | `fix_cycle_started` | `workItems[id].fixCycle = payload.cycle` (badge) |
  | `work_item_needs_human` | `workItems[id].status = 'needs_human'` |
  | `work_item_completed` | `workItems[id].status = 'completed'` |
  | `gate_requested` | `workItems[id].pendingGate = payload.gate` |
  | `gate_approved` | `workItems[id].pendingGate = null`; `status = 'active'` |
  | `gate_rejected` | `workItems[id].pendingGate = null`; keep `payload.note` on the item for display |

  Unknown event types: stored in the timeline, no reduction. (Forward-compatible
  with events later efforts add.)

- The `worker_sessions` array from `hydrateItem` is authoritative and replaces the
  reduced set — the reduced rows are a best-effort live overlay between fetches.

### 3.3 WS client (`ws.ts`)

- `connect()` opens `ws://${location.host}/ws/events?after_seq=${store.lastSeq}`.
- `onopen` → `setConnection('open')`.
- `onmessage` → `store.applyEvent(JSON.parse(e.data))`.
- `onclose` / `onerror` → `setConnection('reconnecting')`, reconnect after
  backoff (1s → 2s → 5s → 10s cap) with the **current** `store.lastSeq` so no
  events are missed across the gap (`05` §2 reconnect).
- Single connection for the app lifetime; opened once in `main.tsx` after
  `bootstrap()`.

### 3.4 API client (`api.ts`)

`listWorkItems()`, `getWorkItem(id)`, `getEvents(id, afterSeq)`,
`createWorkItem({repo, title, chain_template?})`, `approveGate(id, gate)`,
`rejectGate(id, gate, note)`, `getTemplates()`, `getHealth()`,
`logUrl(sid)` (returns the `GET /worker-sessions/{sid}/log` URL for the modal).
All JSON, no auth header. Non-2xx → throw with the response body's `detail`.

### 3.5 Views

**`<Board>` (`/`)** — `05` §4.1 minus repo cluster / `involved_repo` filter.
- Card list from `workItems`. Card: repo tag, title, status badge
  (`active`/`needs_human`/`completed`), `<ChainStrip>` (nodes from
  `chain_definition`, current highlighted, ⚑ on `gate_after` nodes, task-count
  badge on multi-task nodes when active, fix-cycle badge from `item.fixCycle`).
- Client-side filters: repo, status, `chain_template` (dropdowns from the
  loaded set).
- "New Work Item" button → `<IntakeModal>`.

**`<IntakeModal>`** — `05` §4.1a core fields only.
- `repo` (free text — no connected-repo API yet; server 422 on a bad path shows
  inline), `title` (free text), `chain_template` (dropdown from `getTemplates()`,
  default `quick-task`).
- Submit → `createWorkItem` → on 201 close + `navigate('/work-items/' + id)`; the
  store also picks up the `chain_loaded` / `work_item_created` events over WS.
- On failure: inline error, modal stays open with inputs intact.

**`<WorkItemDetail>` (`/work-items/:id`)** — `05` §4.2 minus repos panel and
pause/steer.
- On mount: `store.hydrateItem(id)`; `setInterval(hydrateItem, 60_000)`;
  clear on unmount.
- `<ChainStepper>` — `<ChainStrip size="lg">`: every node a step, current
  highlighted, completed nodes a status icon (clean vs capped-out inferred from
  session statuses at that node), gate marker inline at its node.
- `<CurrentNodePanel>` — one row per `sessionsByItem[id]` session at
  `current_node_id`, every state (not filtered to running). Status chips:
  `running` (pulsing), `completed`, `failed`, `capped_out`, `paused`, `unknown`.
  Fix task row badged `fix · cycle N`. Awaiting-gate state inferred client-side
  (all current-node sessions terminal-clean + `gate_after` set + no next-node
  session) → renders `<Gate>`.
- `<EventTimeline>` — reverse-chronological `eventsByItem[id]`. Each
  `worker_session_*` event has a "view log" link → `<LogModal sid>`.
- `<LogModal>` — `fetch(logUrl(sid))` → text in a `<pre>`. Pull-based, not a live
  tail (`05` §4.2, §6).
- `<Gate>` — Approve → `approveGate`; Reject → requires a non-empty note
  (`05` §4.5) before the submit button enables → `rejectGate`. After either, the
  WS `gate_approved` / `gate_rejected` event updates the store.

**`<ConnBadge>`** — small indicator: `open` (unobtrusive / hidden), `connecting`
/ `reconnecting` (visible "reconnecting…", non-blocking — `05` §2).

**`<HealthBadge>`** — `getHealth()` on load + every 60s; if `status: 'degraded'`
(invalid templates or policy), a persistent header flag with the list (`05` §5).

### 3.6 Routing

`react-router-dom` — two routes (`/`, `/work-items/:id`) plus the modal as
non-route UI state. Chosen over hand-rolling for back-button + deep-link
correctness; one dependency.

---

## 4. Data flow

```
executor / api write ──▶ Database._run: commit ──▶ on_commit() ──▶ Broadcaster.notify()
                                                                        │ Event.set()
                                          fan-out task ◀────────────────┘
                                          reads events.read_after(cursor)
                                          put_nowait ──▶ per-client Queue
WS /ws/events handler ◀── Queue.get() ──▶ socket ──▶ browser ws.onmessage
                                                      store.applyEvent
                                                      React re-render
```

Bootstrap: `GET /work-items` (items + cursor) → store → open WS with
`after_seq=cursor`. Detail view: `GET /work-items/{id}` + `GET
/work-items/{id}/events` authoritative hydrate, 60s refetch as the missed-event
guard.

---

## 5. Error handling

- **Broken `on_commit` listener** — logged, swallowed; writer unaffected.
- **Slow / stuck WS client** — queue fills at 1000, client flagged `dropped`,
  socket closed; browser reconnects and catches up via `after_seq`. No
  back-pressure onto the writer or other clients.
- **WS drop** — client reconnects with tracked `lastSeq`; `after_seq` replay
  covers the gap. `<ConnBadge>` shows "reconnecting…".
- **Missed event despite the feed** — the 60s Detail refetch reconciles; the
  Board reconciles on navigation (re-`bootstrap` is cheap at this scale).
- **API 4xx/5xx** — surfaced inline (intake modal, gate reject); never a silent
  swallow.
- **`frontend/dist` absent** — API serves no SPA, all endpoints still work
  (tests rely on this).

---

## 6. Testing

### 6.1 Backend — `tests/test_ws.py` (new)

- `on_commit` fires after a committed write and not after a failed one.
- `Broadcaster` fans a committed event to two registered clients; `cursor`
  advances.
- Queue overflow flags the client `dropped` and stops feeding it, others
  unaffected.
- `WS /ws/events` end to end (FastAPI `TestClient` websocket): connect with
  `after_seq=0`, drive a write, receive the frame.
- Reconnect: connect, receive up to seq N, disconnect, write more, reconnect with
  `after_seq=N`, receive N+1… with no gap and no duplicate.
- Catch-up / live boundary: events written between `register()` and the
  catch-up read are delivered exactly once.
- Origin check: handshake with a non-localhost `Origin` is rejected.

### 6.2 Backend — `tests/test_api.py` (extend)

- `GET /work-items` — shape, `chain_definition` parsed, `cursor` = max seq.
- `GET /templates` — only resolvable ids, sorted.
- Catch-all SPA route: present when a fake `frontend/dist/index.html` exists in a
  tmp dir (via `KRAFT_*` env or monkeypatch), absent otherwise; API routes still
  win over the catch-all.

### 6.3 Frontend — Vitest + React Testing Library

- `store.applyEvent` — one assertion per event type in the §3.2 table, plus
  unknown-type pass-through and `lastSeq` monotonicity.
- `ws.ts` reconnect — mock `WebSocket`; `onclose` schedules a reconnect with the
  current `lastSeq`; backoff escalates.
- `<Board>` — renders a card per item, filters narrow the list, "New Work Item"
  opens the modal.
- `<IntakeModal>` — submit calls `createWorkItem`; a rejected promise shows the
  inline error and keeps the modal open.
- `<WorkItemDetail>` — hydrates on mount; renders stepper + current-node chips
  from store state; `<Gate>` reject submit disabled until a note is entered.
- `<CurrentNodePanel>` — each status chip class; fix-task badge.

### 6.4 e2e — one Playwright spec

Real `uvicorn` + built `dist/` + fake agent (`KRAFT_FAKE_AGENT`) + `isolated_bd`.
Open the Board, click "New Work Item", submit `quick-task` against the sample
repo, watch the chain strip advance `env_setup → implementation → verify` and the
status badge reach `completed`. Marked so it is excluded from the CI fast tier
(same treatment as the existing e2e / slow tests).

### 6.5 CI — `.gitlab-ci.yml`

New `frontend` job: node image, `cd frontend && npm ci && npm run build && npm
run test`. The Playwright e2e runs only in a nightly / manual job (needs a real
`claude` or the fake-agent wiring + a browser), consistent with `-m e2e` today.

---

## 7. Build order (chunks for `writing-plans`)

**3A — Backend transport.** `Database.on_commit`; `kraft.ws.Broadcaster`;
`WS /ws/events`; `GET /work-items`; `GET /templates`; static/SPA serving;
`test_ws.py` + `test_api.py` extensions. No frontend. Done when the WS suite is
green and the API serves the new endpoints.

**3B — Frontend SPA.** Depends on 3A. Vite/React/TS app; store + ws + api;
Board, IntakeModal, WorkItemDetail and components; Vitest + RTL suite; Playwright
e2e; `.gitlab-ci.yml` frontend job; `.gitignore`. Done when `npm run build`
produces `dist/`, the Vitest suite is green, and the e2e drives a `quick-task` to
`completed` through the UI.

---

## 8. Interfaces touched

- `kraft.db.Database.__init__` — new keyword-only `on_commit`. Existing callers
  (`Database.open`) thread it through; `open(path, *, on_commit=None)`.
- `kraft.db.Database.open` — new keyword-only `on_commit`.
- `kraft.api.lifespan` — constructs `Broadcaster`, passes `broadcaster.notify` as
  `on_commit`, `await broadcaster.start()` / `stop()` around `yield`.
- New: `kraft.ws` module; `WS /ws/events`, `GET /work-items`, `GET /templates`
  routes; static mounts.
- No schema change. No `events` / `store` / `executor` change.

---

## 9. Deferred (own efforts)

| Deferred | Effort |
|---|---|
| Auth `/login` `/logout`, 401 boot path, logout button | when LAN binding lands (`02` §9) |
| `POST /pause` `/steer` `/resume` + pause/steer/resume controls, steerable-hook table | `02` §10.2 mechanism effort |
| Repos panel, repo cluster, `involved_repo` filter, cross-repo intake disclosure | #6 federation |
| Search overlay, bead search box, Document Viewer, `/search` `/beads/search` `/documents` | #4 indexer + search |
| System-status reindex control `POST /index/rescan` | #4 |
| `usage_reported_json` cost/token chip detail | when the MCP status callback ships |
| Mobile / phone-condensed status view | v1.1, not designed |
| Promote steerable-hook mapping to registry `interactive: bool` | `05` §4.2 note, not urgent |

---

## 10. Open questions

- Exact iconography for `capped_out` vs `failed` vs `unknown` and the fix-task /
  task-count badges — cosmetic, settled during 3B (`05` §8).
- Component library / CSS approach beyond plain CSS — none planned; revisit only
  if the hand-rolled CSS becomes unmanageable.
- Whether `GET /work-items` should paginate — no, the list is small by design
  (`02` §10.1); revisit if a real deployment has hundreds of items.
