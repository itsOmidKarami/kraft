import { clock, elapsed } from "../../format";
import type { Finding, KraftEvent } from "../../types";

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
  if (e.type === "work_item_blocked_by_dependency" && Array.isArray(p.blocked_by)) {
    return `blocked on ${(p.blocked_by as string[]).join(", ")}`;
  }
  if (e.type === "paused_by_broken_base" && typeof p.broken_by === "string") {
    const bead = typeof p.follow_up_bead === "string" ? p.follow_up_bead : null;
    return bead
      ? `paused: ${p.broken_by} broke the base it rebased onto (${bead})`
      : `paused: ${p.broken_by} broke the base it rebased onto`;
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
    // "measured", not "findings" bare: this is every finding this one run
    // produced, at any severity -- a different count from the gate card's
    // "N findings deferred" (loop-severity ones excluded, deduped across
    // every run), and the two must read as different things, not a mismatch.
    const base = n > 0 ? `${n} findings measured` : "no findings";
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
  if (e.type === "judge_verdict" && typeof p.reasoning === "string") {
    return p.reasoning || null;
  }
  return null;
}

/** The findings a `findings_measured` event carries, for the Events pane to
 *  list -- `detailOf` above only ever gives a count, and GateCard's own
 *  one-liner (Kraft-a4js) deliberately does the same, so this is the only
 *  place the messages/files/severities themselves become reachable. */
export function findingsOf(e: KraftEvent): Finding[] {
  if (e.type !== "findings_measured") return [];
  const p = e.payload as Record<string, unknown>;
  if (!Array.isArray(p.findings)) return [];
  return (p.findings as Record<string, unknown>[]).map((f) => ({
    severity: typeof f.severity === "string" ? f.severity : "",
    message: typeof f.message === "string" ? f.message : "",
    file: typeof f.file === "string" ? f.file : null,
    line: typeof f.line === "number" ? f.line : null,
    source_plugin: typeof f.source_plugin === "string" ? f.source_plugin : "",
  }));
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
  if (e.type === "judge_verdict" && typeof e.payload.verdict === "string") {
    return e.payload.verdict === "continue" ? "judge: continuing" : "judge: stopped early";
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
  // Every group is named (W8.3): what happened before the first node is
  // "created", never "—". The item's own end (work_item_completed/abandoned,
  // no node_id) stays in the last node's group -- the Events pane shows the
  // selected node's group, and that is where a reader (and chain.spec /
  // lifecycle.spec) looks for the terminal row.
  let node = "created";
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
