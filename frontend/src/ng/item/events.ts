import type { KraftEvent } from "../../types";
import { taskName } from "./paths";

const s = (v: unknown) => (v == null ? "" : String(v));
const cut = (t: string, n = 70) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);

/** Bookkeeping that would drown the run's story in the chain pane's Recent. */
const QUIET = new Set(["worker_session_created", "worker_session_exited", "chain_loaded", "plan_progress", "worktree_prepared", "steer_context_set", "permission_decision", "external_wait_observed", "findings_measured"]);

/** One line of the chain pane's Recent (Decisions §5 Chain pane), or null for
 *  bookkeeping. An event type not listed here shows its own name. */
export function eventLine(e: KraftEvent): string | null {
  if (QUIET.has(e.type)) return null;
  const p = e.payload ?? {};
  const node = s(e.node_id ?? p.node_id);
  switch (e.type) {
    case "work_item_created": return "filed";
    case "node_started": return `${node} started`;
    case "node_completed": return `${node} finished`;
    case "node_skipped": return `${node} skipped`;
    case "worker_session_started": return p.hook_point ? `${taskName(s(p.hook_point))} is running on ${node}` : `${node} is running`;
    case "gate_requested": return `${s(p.gate) || node} is waiting for you`;
    case "gate_approved": return `${s(p.gate) || node} · ${p.by === "agent" || p.by === "kraft" ? "passed on its own" : "approved by you"}`;
    case "gate_rejected": return `${s(p.gate) || node} · rejected${p.note ? `: ${cut(s(p.note), 50)}` : ""}`;
    case "fix_cycle_started": return `${node} · fix loop round ${Number(p.cycle ?? 0) + 1}`;
    case "work_item_needs_human": return `stopped${node ? ` at ${node}` : ""}: ${cut(s(p.reason).replace(/^needs_context:\s*/, ""))}`;
    case "escalation_message": return `escalation${node ? ` on ${node}` : ""}: ${cut(s(p.message), 60)}`;
    case "pause_requested": return "paused";
    case "work_item_resumed": return "resumed";
    case "work_item_retried": return `retried${node ? ` ${node}` : ""}`;
    case "work_item_rate_limited": return `${node} hit a rate limit`;
    case "work_item_waiting": return `${node} is waiting`;
    case "work_item_cancelled": return "cancelled";
    case "work_item_completed": return "finished";
    case "work_item_archived": return "archived";
    case "work_item_restored": return "restored";
    case "mr_opened": return `MR !${s(p.number)} opened`;
    case "mr_closed": return `MR !${s(p.ref)} closed${p.by && p.by !== "cancel" ? ` by ${s(p.by)}` : ""}`;
    case "mr_reopened": return `MR !${s(p.ref)} reopened`;
    case "budget_raised": case "budget_changed": return `budget set to $${s(p.budget_usd)}`;
    case "work_item_title_edited": return "title edited";
    case "work_item_description_edited": return "brief edited";
    default: return e.type.replaceAll("_", " ");
  }
}

/** "now", "6m", "1h 10m": the Recent column's age. */
export function age(iso: string, now = Date.now()): string {
  const m = Math.floor((now - Date.parse(iso)) / 60_000);
  if (!(m >= 1)) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${h}h`;
}
