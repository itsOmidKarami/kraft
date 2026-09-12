import { clock, elapsed } from "../../format";
import type { KraftEvent } from "../../types";

/**
 * Shared by `Inspector/Timeline.tsx` (one row per node that has run) and
 * `RightPane/Events.tsx` (the event list for the selected one) — lifted out
 * of the old `EventTimeline.tsx` unchanged.
 */

export function detailOf(e: KraftEvent): string | null {
  const p = e.payload as Record<string, unknown>;
  // Kraft-qqz8: "3 of 6" beside the "Started task 3 — <title>" titleOf gives it.
  if (e.type === "task_progress" && typeof p.task === "number" && typeof p.total === "number") {
    return `${p.task} of ${p.total}`;
  }
  if (e.type === "gate_rejected" && typeof p.note === "string") return p.note;
  if (e.type === "work_item_needs_human" && typeof p.reason === "string") return p.reason;
  if (e.type === "work_item_rate_limited" && typeof p.retry_at === "string") {
    return `retries at ${p.retry_at}`;
  }
  if (e.type === "work_item_waiting" && typeof p.retry_at === "string") {
    return `waiting on CI, next check at ${p.retry_at}`;
  }
  if (e.type === "worker_session_exited") {
    const bits: string[] = [];
    if (typeof p.status === "string") bits.push(p.status);
    if (typeof p.wall_ms === "number") bits.push(elapsed(p.wall_ms));
    if (typeof p.concerns === "string") bits.push(p.concerns);
    return bits.length > 0 ? bits.join(" · ") : null;
  }
  if (e.type === "findings_measured" && Array.isArray(p.findings)) {
    const n = (p.findings as unknown[]).length;
    const base = n > 0 ? `${n} findings` : "no findings";
    const noop = Array.isArray(p.noop_hooks) ? (p.noop_hooks as string[]) : [];
    return noop.length > 0 ? `${base} · not reviewed (noop): ${noop.join(", ")}` : base;
  }
  if (e.type === "notification_failed") {
    const why = p.status ?? p.error ?? "no response";
    return `${p.event_type} → ${p.host} · ${why}`;
  }
  if (e.type === "fix_cycle_started" && Array.isArray(p.failed_tasks)) {
    const failed = (p.failed_tasks as string[]).join(", ");
    return `cycle ${p.cycle}: ${failed || "findings only"}`;
  }
  if (e.type === "sweep_failed" && typeof p.error === "string") {
    return `${p.task_hook}: ${p.error}`;
  }
  if (e.type === "node_recovery_started" && Array.isArray(p.tasks)) {
    const failed = Array.isArray(p.failed_tasks) ? (p.failed_tasks as string[]).join(", ") : "";
    return `${failed} failed → ${(p.tasks as string[]).join(", ")}`;
  }
  // UI v2 · 04: the reset/override/budget events this MR adds get a plain
  // one-line rendering too, the same "don't ship a blank row" rule as above.
  if (e.type === "node_overrides_changed") {
    const n = Object.keys((p.overrides as Record<string, unknown>) ?? {}).length;
    return n > 0 ? `${n} node override${n === 1 ? "" : "s"}` : "reset to template";
  }
  if (e.type === "budget_changed" || e.type === "budget_raised") {
    return p.budget_usd == null ? "no cap" : `$${Number(p.budget_usd).toFixed(2)}`;
  }
  return null;
}

const VERBS: Record<string, string> = {
  worker_session_created: "created",
  worker_session_started: "started",
  worker_session_exited: "exited",
};

export function titleOf(e: KraftEvent, hooks: Map<string, string>): string | null {
  // Kraft-qqz8: "Started task 3 — <title>", not the generic session verbs below.
  if (e.type === "task_progress" && typeof e.payload.task === "number") {
    return `Started task ${e.payload.task} — ${e.payload.title as string}`;
  }
  const verb = VERBS[e.type];
  if (!verb) return null;
  const p = e.payload as Record<string, unknown>;
  const hook =
    typeof p.hook_point === "string"
      ? p.hook_point
      : typeof p.session_id === "string"
        ? hooks.get(p.session_id)
        : undefined;
  return hook ? `${hook} ${verb}` : null;
}

export interface NodeGroup {
  node: string;
  events: KraftEvent[];
  span: string;
}

/** Most events carry a `node_id`; the ones that don't belong to whichever
 *  node was running when they landed. */
export function groupByNode(events: KraftEvent[]): NodeGroup[] {
  const order: string[] = [];
  const byNode = new Map<string, KraftEvent[]>();
  let node = "—";
  for (const e of events) {
    const id = (e.payload as Record<string, unknown>).node_id;
    if (typeof id === "string") node = id;
    if (!byNode.has(node)) {
      byNode.set(node, []);
      order.push(node);
    }
    byNode.get(node)!.push(e);
  }
  return order.reverse().map((n) => {
    const rows = byNode.get(n)!;
    const from = clock(rows[0].created_at);
    const to = clock(rows[rows.length - 1].created_at);
    return { node: n, events: [...rows].reverse(), span: from === to ? from : `${from} – ${to}` };
  });
}
