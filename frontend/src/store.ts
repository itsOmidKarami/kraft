import { create } from "zustand";
import * as api from "./api";
import { deriveState, type DerivedState } from "./deriveState";
import type { KraftEvent, WorkItem, WorkerSession } from "./types";

type Connection = "connecting" | "open" | "reconnecting";

interface State {
  workItems: Record<string, WorkItem>;
  sessionsByItem: Record<string, WorkerSession[]>;
  eventsByItem: Record<string, KraftEvent[]>;
  lastSeq: number;
  connection: Connection;
  /** Bumped on every work_item_archived/restored event so a count fetched
   *  from the archive endpoint (Header's breadcrumb, the board's Archived
   *  chip) knows to refetch instead of only refreshing after its own click. */
  archivedVersion: number;
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
  archivedVersion: 0,

  setConnection: (connection) => set({ connection }),

  bootstrap: async () => {
    const { items, cursor } = await api.listWorkItems();
    set({
      workItems: Object.fromEntries(items.map((i) => [i.id, i])),
      lastSeq: cursor,
    });
    // W11 · J: whether a needs_human item has an escalation turn running lives
    // in its sessions, which the list does not carry. Hydrate those items so
    // the board, the sidebar badge and the bottom nav can tell escalating from
    // needs-you without opening each one.
    for (const i of items) if (i.status === "needs_human") get().hydrateItem(i.id).catch(() => {});
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
    const budget =
      (stopEv?.type === "work_item_needs_human"
        ? (stopEv.payload.budget as WorkItem["budget"])
        : null) ?? null;
    set((s) => ({
      workItems: {
        ...s.workItems,
        [id]: {
          ...s.workItems[id],
          ...item,
          completedNodes,
          cappedOut,
          budget,
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
            ...patchItem(s, id, (w) => ({
              ...w,
              current_node_id: p.node_id,
              cappedOut: null,
              needs_context_question: null,
              budget: null,
            })),
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
            attempt: p.attempt ?? 1,
            thread: p.thread ?? 1,
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
            attempt: p.attempt ?? 1,
            thread: p.thread ?? 1,
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
        // Kraft-qqz8: the hero task bar and the Tasks tab's plan list update
        // the moment the implementer reports, not on the next 60s poll. The
        // full `tasks` list (each one's own state) is `combine()`'s to build
        // server-side — patched here from what the event already carries
        // when there is a prior list to carry forward, and backfilled by the
        // queued hydrate otherwise (first report of a fresh run).
        case "task_progress":
          return {
            ...base,
            ...patchItem(s, id, (w) => ({
              ...w,
              progress: {
                current: p.task as number,
                total: p.total as number,
                title: p.title as string,
                tasks:
                  w.progress?.tasks?.map((t) => ({
                    ...t,
                    state: (t.n < p.task ? "done" : t.n === p.task ? "current" : "pending") as
                      | "done"
                      | "current"
                      | "pending",
                  })) ?? [],
              },
            })),
          };
        case "gate_requested":
          // store.request_gate flips the row to needs_human in the same
          // transaction that appends this event, so mirror it here — otherwise
          // the board badge reads "active" until the next hydrate (Kraft-fnx).
          // Also re-hydrate: `deferred_findings` is detail-only and otherwise
          // only refreshes on the 60s poll, so a detail view already open when
          // the review node finishes would offer Approve beside a stale
          // (possibly empty) roll-up.
          queueMicrotask(() => get().hydrateItem(id).catch(() => {}));
          return {
            ...base,
            ...patchItem(s, id, (w) => ({ ...w, pending_gate: p.gate, status: "needs_human" })),
          };
        case "gate_approved":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, pending_gate: null, status: "active" })) };
        case "gate_rejected":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, pending_gate: null, rejectNote: p.note })) };
        case "pause_requested":
        // Both of these flip the row to paused in the same transaction that
        // appends the event (store/work_items.py), same as pause_requested --
        // without a case here the board reads "active" until the next hydrate.
        case "work_item_blocked_by_dependency":
        case "paused_by_broken_base":
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
              needs_context_question: null,
              budget: null,
            })),
          };
        case "work_item_needs_human":
          return {
            ...base,
            ...patchItem(s, id, (w) => ({
              ...w,
              status: "needs_human",
              cappedOut: (p.capped as WorkItem["cappedOut"]) ?? null,
              needs_context_question: questionOf(p.reason as string | undefined),
              budget: (p.budget as WorkItem["budget"]) ?? null,
            })),
          };
        case "work_item_rate_limited":
          return {
            ...base,
            ...patchItem(s, id, (w) => ({
              ...w,
              status: "rate_limited",
              retry_at: p.retry_at as string,
            })),
          };
        case "work_item_waiting":
          return {
            ...base,
            ...patchItem(s, id, (w) => ({
              ...w,
              status: "waiting",
              retry_at: p.retry_at as string,
            })),
          };
        case "work_item_completed":
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, status: "completed" })) };
        case "work_item_abandoned":
          // A live board would otherwise keep offering actions on a worktree
          // that has already been removed.
          return { ...base, ...patchItem(s, id, (w) => ({ ...w, status: "abandoned" })) };
        case "work_item_archived":
          return {
            ...base,
            archivedVersion: s.archivedVersion + 1,
            ...patchItem(s, id, (w) => ({
              ...w,
              archived_at: ev.created_at,
              archived_by: p.by as "you" | "auto",
            })),
          };
        case "work_item_restored":
          return {
            ...base,
            archivedVersion: s.archivedVersion + 1,
            ...patchItem(s, id, (w) => ({ ...w, archived_at: null, archived_by: null })),
          };
        default:
          return base;
      }
    });
  },
}));

/**
 * The question inside a `work_item_needs_human` reason, or null for a stop of
 * any other kind. Without this the SPA learns a needs_context stop only from
 * the next hydrate: the live stream flips the status, no question and no answer
 * box appear, and on a chain with no fix-loop node the control row's Steer is
 * hard-disabled -- the exact dead end the needs_context status exists to remove.
 */
function questionOf(reason: string | undefined): string | null {
  if (!reason?.startsWith("needs_context:")) return null;
  return reason.slice("needs_context:".length).trim();
}

/** The columns an event does not carry; the next hydrate fills them in. */
function blankSession(workItemId: string, createdAt: string): WorkerSession {
  return {
    id: "",
    work_item_id: workItemId,
    node_id: "",
    hook_point: "",
    status: "pending",
    attempt: 1,
    thread: 1,
    round: 0,
    created_at: createdAt,
    started_at: null,
    exited_at: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    wall_ms: null,
    model: null,
    head_sha: null,
  };
}

/** `deriveState` with the sessions and events this client already holds for
 *  each item — without them a list item's running escalation turn reads as
 *  the stop underneath it (W11 · J). */
export function useItemStates(): (item: WorkItem) => DerivedState {
  const sessions = useStore((s) => s.sessionsByItem);
  const events = useStore((s) => s.eventsByItem);
  return (item) => deriveState(item, sessions[item.id] ?? [], events[item.id] ?? []);
}

/** A session by id, whichever work item it belongs to — the log pane has only the id. */
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
