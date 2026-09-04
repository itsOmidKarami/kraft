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
    const completedNodes = [
      ...new Set(
        evs
          .filter((e) => e.type === "node_completed")
          .map((e) => e.payload.node_id as string),
      ),
    ];
    const fixEv = [...evs].reverse().find((e) => e.type === "fix_cycle_started");
    const fixCycle = fixEv ? (fixEv.payload.cycle as number) : undefined;
    // The cap numbers ride work_item_needs_human; a later node_started clears them.
    const stopEv = [...evs]
      .reverse()
      .find((e) => e.type === "work_item_needs_human" || e.type === "node_started");
    const cappedOut =
      (stopEv?.type === "work_item_needs_human"
        ? (stopEv.payload.capped as WorkItem["cappedOut"])
        : null) ?? null;
    set((s) => ({
      workItems: {
        ...s.workItems,
        [id]: {
          ...s.workItems[id],
          ...item,
          completedNodes,
          cappedOut,
          ...(fixCycle !== undefined ? { fixCycle } : {}),
        },
      },
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
          if (!s.workItems[id]) queueMicrotask(() => { get().hydrateItem(id).catch(() => {}); });
          return base;
        case "node_started":
          return {
            ...base,
            ...patchItem(s, id, (w) => ({ ...w, current_node_id: p.node_id, cappedOut: null })),
          };
        case "node_completed":
          return {
            ...base,
            ...patchItem(s, id, (w) => ({
              ...w,
              completedNodes: [...new Set([...(w.completedNodes ?? []), p.node_id])],
            })),
          };
        // Emitted the moment the row is created, whatever the hook kind. A builtin
        // hook never reaches worker_session_started, and worker_session_exited has
        // no node_id/hook_point to rebuild from — so without this the panel saw no
        // session for the current node and rendered no gate until a reload
        // (Kraft-dce).
        case "worker_session_created": {
          const rows = s.sessionsByItem[id] ?? [];
          const row: WorkerSession = {
            ...blankSession(id, ev.created_at),
            id: p.session_id,
            node_id: p.node_id,
            hook_point: p.hook_point,
            status: "pending",
            round: p.round ?? 0,
          };
          return { ...base, sessionsByItem: { ...s.sessionsByItem, [id]: upsert(rows, row) } };
        }
        case "worker_session_started": {
          const rows = s.sessionsByItem[id] ?? [];
          const row: WorkerSession = {
            ...blankSession(id, ev.created_at),
            id: p.session_id,
            node_id: p.node_id,
            hook_point: p.hook_point,
            status: "running",
            round: p.round ?? 0,
            started_at: ev.created_at,
          };
          // upsert merges this over the created row; `round` is carried on both
          // events so the merge cannot reset a fix cycle's session to round 0
          return { ...base, sessionsByItem: { ...s.sessionsByItem, [id]: upsert(rows, row) } };
        }
        case "worker_session_exited":
        case "session_unknown": {
          const rows = s.sessionsByItem[id] ?? [];
          const status = ev.type === "session_unknown" ? "unknown" : p.status;
          // worker_session_exited also carries the usage captured on the way out
          const patch =
            ev.type === "worker_session_exited"
              ? {
                  status,
                  exited_at: ev.created_at,
                  wall_ms: p.wall_ms ?? null,
                  model: p.model ?? null,
                  tokens_in: p.tokens_in ?? null,
                  tokens_out: p.tokens_out ?? null,
                  cost_usd: p.cost_usd ?? null,
                }
              : { status };
          return {
            ...base,
            sessionsByItem: {
              ...s.sessionsByItem,
              [id]: rows.map((r) => (r.id === p.session_id ? { ...r, ...patch } : r)),
            },
          };
        }
        case "fix_cycle_started":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, fixCycle: p.cycle })) };
        case "gate_requested":
          // store.request_gate flips the row to needs_human in the same
          // transaction that appends this event, so mirror it here — otherwise
          // the board badge reads "active" until the next hydrate (Kraft-fnx).
          return {
            ...base,
            ...patchItem(s, id, (w) => ({ ...w, pendingGate: p.gate, status: "needs_human" })),
          };
        case "gate_approved":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, pendingGate: null, status: "active" })) };
        case "gate_rejected":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, pendingGate: null, rejectNote: p.note })) };
        case "pause_requested":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, status: "paused" })) };
        case "worker_session_paused": {
          const rows = s.sessionsByItem[id] ?? [];
          return {
            ...base,
            sessionsByItem: {
              ...s.sessionsByItem,
              [id]: rows.map((r) =>
                r.id === p.session_id ? { ...r, status: "paused" as const } : r,
              ),
            },
          };
        }
        case "steer_context_set":
          return {
            ...base,
            ...patchItem(s, id, (w) => ({ ...w, pending_steer_context: p.steer })),
          };
        case "work_item_resumed":
        case "work_item_retried":
          return {
            ...base,
            ...patchItem(s, id, (w) => ({
              ...w,
              status: "active",
              pending_steer_context: null,
              cappedOut: null,
            })),
          };
        case "work_item_needs_human":
          return {
            ...base,
            ...patchItem(s, id, (w) => ({
              ...w,
              status: "needs_human",
              cappedOut: (p.capped as WorkItem["cappedOut"]) ?? null,
            })),
          };
        case "work_item_completed":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, status: "completed" })) };
        default:
          return base;
      }
    });
  },
}));

/** The columns an event does not carry; the next hydrate fills them in. */
function blankSession(workItemId: string, createdAt: string): WorkerSession {
  return {
    id: "",
    work_item_id: workItemId,
    node_id: "",
    hook_point: "",
    status: "pending",
    attempt: 1,
    round: 0,
    created_at: createdAt,
    started_at: null,
    exited_at: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    wall_ms: null,
    model: null,
  };
}

/** A session by id, whichever work item it belongs to — the log modal has only the id. */
export function findSession(sid: string): WorkerSession | undefined {
  for (const rows of Object.values(useStore.getState().sessionsByItem)) {
    const hit = rows.find((r) => r.id === sid);
    if (hit) return hit;
  }
  return undefined;
}

function upsert(rows: WorkerSession[], row: WorkerSession): WorkerSession[] {
  const i = rows.findIndex((r) => r.id === row.id);
  if (i === -1) return [...rows, row];
  const copy = rows.slice();
  copy[i] = { ...copy[i], ...row };
  return copy;
}
