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
    // A start, in the past tense: the list keeps it after the session ended, and "is running" read as live (R12b-12).
    case "worker_session_started": return p.hook_point ? `${taskName(s(p.hook_point))} started on ${node}` : `a session started on ${node}`;
    case "gate_requested": return `${s(p.gate) || node} is waiting for you`;
    case "gate_approved": return `${s(p.gate) || node} · ${p.by === "agent" || p.by === "kraft" ? "passed on its own" : "approved by you"}`;
    case "gate_rejected": return `${s(p.gate) || node} · rejected${p.note ? `: ${cut(s(p.note), 50)}` : ""}`;
    case "fix_cycle_started": return `${node} · fix loop round ${Number(p.cycle ?? 0) + 1}`;
    // What the round did, said once it ended; recent() folds it into the round's start line. null: git could not say.
    case "fix_cycle_finished": return `${node} · fix loop round ${Number(p.cycle ?? 0) + 1}${p.committed === true ? " · the fix committed a change" : p.committed === false ? " · the fix changed nothing" : ""}`;
    case "work_item_needs_human": return `stopped${node ? ` at ${node}` : ""}: ${cut(s(p.reason).replace(/^needs_context:\s*/, ""))}`;
    case "escalation_message": return `escalation${node ? ` on ${node}` : ""}: ${cut(s(p.message), 60)}`;
    case "pause_requested": return "paused";
    case "work_item_resumed": return "resumed";
    case "work_item_retried": return `retried${node ? ` ${node}` : ""}`;
    case "work_item_rate_limited": return `${node} hit a rate limit`;
    case "work_item_waiting": return `${node} is waiting`;
    case "work_item_cancelled": return "cancelled";
    case "work_item_completed": return "finished";
    case "work_item_archived": return p.kept_branch ? `archived · kept ${s(p.kept_branch)}: ${s(p.unpushed_commits)} unpushed commit${p.unpushed_commits === 1 ? "" : "s"}` : "archived";
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

export type RecentLine = { e: KraftEvent; line: string };

/** Recent's story, newest first (WI-2): a session's start and end fold into one line that
 *  leads with what changed. A live session reads "code_review is running on verification";
 *  one that ended well drops out, the node's next line tells it; one that failed says so at
 *  its end; a gate's decision takes the place of its request; a fix round's outcome joins its start line; an escalation's turn that ends reads "escalation answered". A node's start drops
 *  once a session on it starts, and its finish takes the place of everything that ran in it. */
export function recent(events: KraftEvent[]): RecentLine[] {
  const out: (RecentLine & { node?: string; session?: string })[] = [];
  const started = new Map<string, { node: string; task: string }>();
  const drop = (keep: (x: (typeof out)[number]) => boolean) => out.splice(0, out.length, ...out.filter(keep));
  for (const e of events) {
    const p = e.payload ?? {};
    const node = s(e.node_id ?? p.node_id);
    if (e.type === "worker_session_started") {
      const task = p.hook_point ? taskName(s(p.hook_point)) : "a session";
      started.set(s(p.session_id), { node, task });
      drop((x) => !(x.node === node && x.e.type === "node_started"));
      out.push({ e, node, session: s(p.session_id), line: `${task} is running on ${node}` });
    } else if (e.type === "worker_session_exited") {
      const at = started.get(s(p.session_id));
      drop((x) => x.session !== s(p.session_id));
      if (!at) continue;
      const ok = s(p.status).startsWith("done");
      if (at.task === "escalation") out.push({ e: { ...e, node_id: at.node || null }, node: at.node, line: ok ? "escalation answered" : `escalation ${s(p.status).replaceAll("_", " ")}` });
      else if (!ok && p.status !== "paused") out.push({ e: { ...e, node_id: at.node || null }, node: at.node, line: `${at.task} ${s(p.status).replaceAll("_", " ")} on ${at.node}` });
    } else {
      const line = eventLine(e);
      if (!line) continue;
      // A round's outcome joins its start line; with no start left to join, it stands alone.
      const round = e.type === "fix_cycle_finished" && out.find((x) => x.e.type === "fix_cycle_started" && x.node === node && x.e.payload?.cycle === p.cycle);
      if (round) { round.line = line; continue; }
      const ran = (x: (typeof out)[number]) => x.e.type === "node_started" || (x.e.type.startsWith("worker_session") && !x.line.startsWith("escalation"));
      if (e.type === "node_completed" || e.type === "node_skipped") drop((x) => x.node !== node || !ran(x));
      // A decision answers the request: the wait is over and the line that said so goes (CG-3).
      if (e.type === "gate_approved" || e.type === "gate_rejected") drop((x) => !(x.e.type === "gate_requested" && (s(x.e.payload?.gate) || x.node) === (s(p.gate) || node)));
      out.push({ e, node, line });
    }
  }
  return out.reverse().map(({ e, line }) => ({ e, line }));
}

/** "now", "6m", "1h 10m": the Recent column's age. */
export function age(iso: string, now = Date.now()): string {
  const m = Math.floor((now - Date.parse(iso)) / 60_000);
  if (!(m >= 1)) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${h}h`;
}
