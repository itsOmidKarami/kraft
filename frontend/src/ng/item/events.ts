import type { KraftEvent } from "../../types";
import type { EventType } from "../../types/vocab.generated";
import { elapsed } from "../../format";
import { gateName, nameOf, queuedVerbWords, waitingForCheckout } from "../board/rowText";
import { taskName } from "./paths";

type Nodes = Parameters<typeof nameOf>[0];

const s = (v: unknown) => (v == null ? "" : String(v));
const cut = (t: string, n = 70) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);

/** Bookkeeping that would drown the run's story in the chain pane's Recent. */
const QUIET = new Set<EventType>(["worker_session_created", "worker_session_exited", "chain_loaded", "plan_progress", "worktree_prepared", "steer_context_set", "permission_decision", "external_wait_observed", "findings_measured", "run_forked", "cap_counters_reset", "session_reattached"]);

/** What reopened an approved gate (`gate_reopened`'s `reason`), as the console says it. */
const REOPENED_WHY: Readonly<Record<string, string>> = {
  retry: " by the retry",
  conflict_resolved: " after a conflict was resolved",
  base_changed: " after a base change",
  merge_rejected: " after the merge train or queue rejected it",
  escalation_committed: " after an escalation committed a change",
  read_only_violated: " after a read-only step changed files",
  unreviewed_change: " because the code changed after it was approved",
};

/** How Kraft itself moved the approved head (`reviewed_head_repinned`'s `cause`), as the console says it. */
const REPINNED_HOW: Readonly<Record<string, string>> = {
  rebase: " with Kraft's own rebase",
  pointer_bump: " with Kraft's own pointer bump",
};

/** A cancel's own status write: `store.end_work_item` follows `work_item_cancelled` with `work_item_abandoned`.
 *  The cancel tells it; "abandoned" alone is the other door, the abandon that deletes the worktree and branch. */
export const cancelsOwn = (e: KraftEvent, before?: KraftEvent) => e.type === "work_item_abandoned" && before?.type === "work_item_cancelled";

/** One line of the chain pane's Recent (Decisions §5 Chain pane), or null for
 *  bookkeeping. An event type not listed here shows its own name. `before` is the
 *  event just before it in the item's history, for the one line that depends on it (`cancelsOwn`). */
export function eventLine(e: KraftEvent, nodes?: Nodes, before?: KraftEvent): string | null {
  if (QUIET.has(e.type) || cancelsOwn(e, before)) return null;
  const p = e.payload ?? {};
  const node = s(e.node_id ?? p.node_id);
  const at = node && nameOf(nodes, node);
  // A gate the event names (`gate`), else the node it is on, each as a reader names it.
  const gate = p.gate ? gateName(nodes, s(p.gate)) : at;
  switch (e.type) {
    case "work_item_created": return "filed";
    case "node_started": return `${at} started`;
    case "node_completed": return `${at} finished`;
    case "node_skipped": return `${at} skipped`;
    // A start, in the past tense: the list keeps it after the session ended, and "is running" read as live (R12b-12).
    case "worker_session_started": return p.hook_point ? `${taskName(s(p.hook_point))} started on ${at}` : `a session started on ${at}`;
    // Activity's words for a session's end with no exit, and a paused task's resume; recent() tells these by the session.
    case "worker_session_paused": return "a session stopped";
    case "session_unknown": return "a session was lost in a restart";
    case "agent_session_resumed": return `${taskName(s(p.task))} resumed its session on ${at}`;
    // A task or a step of a node (`node.step`, `node.step.task`), named as a session's task is.
    case "scope_skipped": {
      const path = s(p.path), head = path.split(".")[0];
      return path.includes(".") ? `${taskName(path)} skipped on ${nameOf(nodes, head)}` : `${nameOf(nodes, head)} skipped`;
    }
    case "gate_requested": return `${gate} is waiting for you`;
    case "gate_approved": return `${gate} · ${p.by === "agent" || p.by === "kraft" ? "passed on its own" : "approved by you"}`;
    case "gate_rejected": return `${gate} · rejected${p.note ? `: ${cut(s(p.note), 50)}` : ""}`;
    // A checks-first reviewer's outcome; any other skip reason keeps the type's own words.
    case "gate_auto_review_skipped":
      if (p.reason === "passed") return `${gate} · reviewer: ready for you`;
      if (p.reason === "undecided") return `${gate} · reviewer could not decide`;
      return e.type.replaceAll("_", " ");
    // An approved gate a retry, a base-change restart, or a merge node sending the item back opened again: the rerun asks it anew.
    case "gate_reopened": return `${gate} · reopened${typeof p.reason === "string" && Object.hasOwn(REOPENED_WHY, p.reason) ? REOPENED_WHY[p.reason] : ""}`;
    case "fix_cycle_started": return `${at} · fix loop round ${Number(p.cycle ?? 0) + 1}`;
    // What the round did, said once it ended; recent() folds it into the round's start line. null: git could not say.
    case "fix_cycle_finished": return `${at} · fix loop round ${Number(p.cycle ?? 0) + 1}${p.committed === true ? " · the fix committed a change" : p.committed === false ? " · the fix changed nothing" : ""}`;
    case "work_item_needs_human": return `stopped${node ? ` at ${at}` : ""}: ${cut(s(p.reason).replace(/^needs_context:\s*/, ""), 240)}`;
    case "escalation_message": return `escalation${node ? ` on ${at}` : ""}: ${cut(s(p.message), 240)}`;
    case "pause_requested": return "paused";
    case "work_item_resumed": return "resumed";
    case "work_item_retried": return `retried${node ? ` ${at}` : ""}`;
    // The walk stopped before any agent took the steer (`walk._report_if_undelivered`).
    case "steer_undelivered": return `steer not delivered${p.steer ? `: ${cut(s(p.steer), 240)}` : ""}`;
    case "work_item_rate_limited": return `${at} hit a rate limit`;
    case "work_item_waiting": return `${at} is waiting`;
    case "work_item_queued": {
      const wait = waitingForCheckout(p);
      return `queued to ${queuedVerbWords(s(p.verb))}${wait ? ` · ${wait}` : ""}`;
    }
    case "work_item_cancelled": return "cancelled";
    case "work_item_completed": return "finished";
    // Mark complete: the reason is said nowhere else. The `work_item_completed` after it still reads "finished".
    case "work_item_manually_completed": return `marked complete${p.reason ? `: ${cut(s(p.reason), 240)}` : ""}`;
    case "work_item_abandoned": return "abandoned";
    case "work_item_archived": return p.kept_branch ? `archived · kept ${s(p.kept_branch)}: ${s(p.unpushed_commits)} unpushed commit${p.unpushed_commits === 1 ? "" : "s"}` : "archived";
    case "work_item_restored": return "restored";
    case "one_off_chain_approved": return "one-off chain approved";
    case "mr_opened": return `MR !${s(p.number)} opened`;
    case "mr_closed": return `MR !${s(p.ref)} closed${p.by && p.by !== "cancel" ? ` by ${s(p.by)}` : ""}`;
    case "mr_reopened": return `MR !${s(p.ref)} reopened`;
    case "merge_refused_unreviewed":
      return p.source === "forge"
        ? `${at} · not merged: a commit reached the merge request after the review; ${p.fast_forwarded ? "it is on the item's branch to review" : "pull it into the branch by hand"}`
        : `${at} · not merged: the code is not what the final review approved`;
    case "merge_landed_unreviewed": return `${at} · the forge merged a head the final review did not approve`;
    case "reviewed_head_repinned": return `reviewed head moved${typeof p.cause === "string" && Object.hasOwn(REPINNED_HOW, p.cause) ? REPINNED_HOW[p.cause] : ""}`;
    case "budget_raised": case "budget_changed": return `budget set to $${s(p.budget_usd)}`;
    // Nothing was merged behind the item, so its beads stay open for the operator to close by hand.
    case "beads_left_open": return `left ${Array.isArray(p.beads) ? p.beads.join(", ") : "its beads"} open: ${cut(s(p.reason), 240)}`;
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
 *  once a session on it starts, and its finish takes the place of everything that ran in it, with how long it ran.
 *  A gate reads once, by its decision (CG-4), and an item's end reads once: in its last node's line, in a person's
 *  "marked complete", or in "cancelled" (`cancelsOwn`).
 *  A session a person stopped ends with `worker_session_paused`, never an exit (`store.session_exited` writes
 *  none for a paused row): it drops out, and the pause, cancel, completion or skip that stopped it tells the story,
 *  except an escalation's turn, which reads "escalation stopped" (Stop agent, or a decision that outranked it).
 *  A session a restart could not confirm (`session_unknown`) says so as a failed one does: "code_review was lost in
 *  a restart on verification" ("escalation was lost in a restart"). A session ends once: a second end for it (a stop
 *  racing a turn that just finished) and a start that lands after its end (`store.session_running` on a row a pause
 *  already settled) add nothing. A paused task that resumes (`agent_session_resumed`) runs as a new session with its
 *  own start, so the resume adds no line of its own: the item's "resumed" says it. */
export function recent(events: KraftEvent[], nodes?: Nodes): RecentLine[] {
  const nm = (id: string) => (id ? nameOf(nodes, id) : id);
  const out: (RecentLine & { node?: string; session?: string })[] = [];
  const started = new Map<string, { node: string; task: string }>();
  const drop = (keep: (x: (typeof out)[number]) => boolean) => out.splice(0, out.length, ...out.filter(keep));
  // Sessions already ended, by any road: a start or an end that comes after reads nothing.
  const ended = new Set<string>();
  /** The session `sid` ends here: its running line goes, and what it was, the first time only. */
  const end = (sid: string) => {
    const at = started.get(sid);
    started.delete(sid);
    ended.add(sid);
    drop((x) => x.session !== sid);
    return at;
  };
  const nodeStart = new Map<string, number>();
  for (const [i, e] of events.entries()) {
    const p = e.payload ?? {};
    const node = s(e.node_id ?? p.node_id);
    if (e.type === "worker_session_started") {
      if (ended.has(s(p.session_id))) continue;
      const task = p.hook_point ? taskName(s(p.hook_point)) : "a session";
      started.set(s(p.session_id), { node, task });
      drop((x) => !(x.node === node && x.e.type === "node_started"));
      out.push({ e, node, session: s(p.session_id), line: `${task} is running on ${nm(node)}` });
    } else if (e.type === "worker_session_exited") {
      const at = end(s(p.session_id));
      if (!at) continue;
      const ok = s(p.status).startsWith("done");
      if (at.task === "escalation") out.push({ e: { ...e, node_id: at.node || null }, node: at.node, line: ok ? "escalation answered" : `escalation ${s(p.status).replaceAll("_", " ")}` });
      else if (!ok && p.status !== "paused") out.push({ e: { ...e, node_id: at.node || null }, node: at.node, line: `${at.task} ${s(p.status).replaceAll("_", " ")} on ${nm(at.node)}` });
    } else if (e.type === "worker_session_paused" || e.type === "session_unknown") {
      // A session that ends with no exit: stopped by a person, or lost to a restart (`reattach`).
      const at = end(s(p.session_id));
      if (!at) continue;
      const how = e.type === "session_unknown" ? "was lost in a restart" : "stopped";
      const here = { e: { ...e, node_id: at.node || null }, node: at.node };
      if (at.task === "escalation") out.push({ ...here, line: `escalation ${how}` });
      else if (e.type === "session_unknown") out.push({ ...here, line: `${at.task} ${how} on ${nm(at.node)}` });
    } else if (e.type === "agent_session_resumed") {
      // The resumed task runs as a new session with a start line of its own; the item's "resumed" tells the resume.
    } else {
      let line = eventLine(e, nodes, events[i - 1]);
      if (!line) continue;
      if (e.type === "node_started") nodeStart.set(node, Date.parse(e.created_at));
      // A round's outcome joins its start line; with no start left to join, it stands alone.
      const round = e.type === "fix_cycle_finished" && [...out].reverse().find((x) => x.e.type === "fix_cycle_started" && x.node === node && x.e.payload?.cycle === p.cycle);
      if (round) { round.line = line; continue; }
      const ran = (x: (typeof out)[number]) => x.e.type === "node_started" || ((x.e.type.startsWith("worker_session") || x.e.type === "session_unknown") && !x.line.startsWith("escalation"));
      if (e.type === "node_completed" || e.type === "node_skipped") drop((x) => x.node !== node || !ran(x));
      if (e.type === "node_completed") {
        // A gate that was decided says so once: its own finish repeats the decision (CG-4).
        if (out.some((x) => (x.e.type === "gate_approved" || x.e.type === "gate_rejected") && (s(x.e.payload?.gate) || x.node) === node)) continue;
        const ms = Date.parse(e.created_at) - (nodeStart.get(node) ?? NaN);
        if (ms >= 1000) line += ` · ${elapsed(ms)}`;
      }
      // The item's end, when its last node just finished, is that node's line; when a person marked it complete, theirs.
      if (e.type === "work_item_completed" && (out.at(-1)?.e.type === "node_completed" || out.at(-1)?.e.type === "work_item_manually_completed")) continue;
      // A decision answers the request: the wait is over and the line that said so goes (CG-3).
      // A skip answers it too (R14b-05): the gate was passed without a decision.
      if (e.type === "gate_approved" || e.type === "gate_rejected" || e.type === "node_skipped") drop((x) => !(x.e.type === "gate_requested" && (s(x.e.payload?.gate) || x.node) === (s(p.gate) || node)));
      out.push({ e, node, line });
    }
  }
  return out.reverse().map(({ e, line }) => ({ e, line }));
}

/** Whether the gate was skipped rather than decided: its last skip with no request or decision after it (R14b-05). */
export function gateSkipped(events: KraftEvent[], gate: string): boolean {
  const at = (type: EventType) => events.reduce((n, x, i) => (x.type === type && (x.payload.gate ?? x.payload.node_id ?? x.node_id) === gate ? i : n), -1);
  return at("node_skipped") > Math.max(at("gate_requested"), at("gate_approved"));
}

/** "now", "6m", "1h 10m": the Recent column's age. */
export function age(iso: string, now = Date.now()): string {
  const m = Math.floor((now - Date.parse(iso)) / 60_000);
  if (!(m >= 1)) return "now";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h}h ${String(m % 60).padStart(2, "0")}m` : `${h}h`;
}
