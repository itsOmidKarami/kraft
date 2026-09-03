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
    set((s) => ({
      workItems: {
        ...s.workItems,
        [id]: {
          ...s.workItems[id],
          ...item,
          completedNodes,
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
