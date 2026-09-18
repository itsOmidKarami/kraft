import { clock, elapsed } from "../../format";
import type { Finding, KraftEvent, WorkerSession } from "../../types";

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
  if (e.type === "loop_counters_reset" && p.reason === "rebase_bounce" && Array.isArray(p.nodes)) {
    return `rebased onto latest main → re-running ${(p.nodes as string[]).join(", ")}`;
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
    reported_severity: typeof f.reported_severity === "string" ? f.reported_severity : undefined,
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

/* ── W13 · C: rounds → sessions ───────────────────────────────────────────── */

/** One pass of a node: opened by `node_started` (round 0) or a
 *  `fix_cycle_started`, closed by the judge's verdict or the next round. */
export interface Round {
  node: string;
  n: number;
  startedAt: string;
  endedAt: string | null;
  sessions: WorkerSession[];
  findings: Finding[];
  verdict: string | null;
  reasoning: string | null;
  /** The round's own events (fix_cycle_started, findings_measured, judge_verdict), oldest first. */
  events: KraftEvent[];
}

/** A row the left list shows at its time: a round, an escalation turn, or a
 *  node-level event (gates, node and item lifecycle, a run of task_progress). */
export type TimelineEntry =
  | { kind: "round"; at: string; round: Round }
  | { kind: "escalationThread"; at: string; thread: number; sessions: WorkerSession[] }
  | { kind: "event"; at: string; event: KraftEvent; label: string; last?: KraftEvent };

export interface NodeRounds {
  node: string;
  rounds: Round[];
  /** Oldest first. */
  entries: TimelineEntry[];
  /** Oldest first. */
  events: KraftEvent[];
  startedAt: string | null;
  endedAt: string | null;
}

const SESSION_TYPES = new Set(["worker_session_created", "worker_session_started", "worker_session_exited", "escalation_message"]);
// node_started opens round 0 and is also a node-level row (C.2's `node_started · 16:02`).
const ROUND_TYPES = new Set(["fix_cycle_started", "findings_measured", "judge_verdict"]);
const sessionOf = (e: KraftEvent) => (typeof e.payload.session_id === "string" ? e.payload.session_id : null);

/** A verdict as C.2/D.2 write it: every `stop…` (stop_needs_human, stop_downgrade) is `stop`. */
export const verdictWord = (verdict: string) => (verdict.startsWith("stop") ? "stop" : verdict);

/** Rounds from one node's events (any order) and its sessions (C.1). Escalation
 *  sessions are not in any round -- `nodeRounds` groups them on their own. */
export function roundsOf(events: KraftEvent[], sessions: WorkerSession[]): Round[] {
  const asc = [...events].sort((a, b) => a.seq - b.seq);
  const node = asc.map((e) => e.payload.node_id).find((v): v is string => typeof v === "string") ?? "";
  const rounds: Round[] = [];
  const open = (e: KraftEvent): Round => {
    const prev = rounds[rounds.length - 1];
    if (prev && !prev.endedAt) prev.endedAt = e.created_at;
    const next: Round = { node, n: rounds.length, startedAt: e.created_at, endedAt: null, sessions: [], findings: [], verdict: null, reasoning: null, events: [] };
    rounds.push(next);
    return next;
  };
  for (const e of asc) {
    const cur: Round | undefined = rounds[rounds.length - 1];
    if (e.type === "node_started") open(e);
    else if (e.type === "fix_cycle_started") open(e).events.push(e);
    else if (e.type === "findings_measured" && cur) {
      cur.findings.push(...findingsOf(e));
      cur.events.push(e);
    } else if (e.type === "judge_verdict" && cur) {
      cur.verdict = typeof e.payload.verdict === "string" ? e.payload.verdict : null;
      cur.reasoning = typeof e.payload.reasoning === "string" ? e.payload.reasoning : null;
      cur.endedAt = e.created_at;
      cur.events.push(e);
    }
  }
  const runs = sessions.filter((s) => s.hook_point !== "escalation").sort((a, b) => a.created_at.localeCompare(b.created_at));
  for (const s of runs) {
    let home: Round | undefined;
    for (const r of rounds) if (r.startedAt <= s.created_at) home = r;
    (home ?? rounds[0])?.sessions.push(s);
  }
  // A round nobody closed ends with the last thing that happened in it.
  for (const r of rounds) {
    if (r.endedAt) continue;
    const times = [...r.events.map((e) => e.created_at), ...r.sessions.map((s) => s.exited_at ?? s.created_at)].sort();
    const running = r.sessions.some((s) => !s.exited_at);
    r.endedAt = running ? null : (times[times.length - 1] ?? r.startedAt);
  }
  return rounds;
}

/** `node_started · 16:02`, `gate_requested · code_review` -- a node-level row's label. */
function eventLabel(e: KraftEvent): string {
  const p = e.payload as Record<string, unknown>;
  if (typeof p.gate === "string") return `${e.type} · ${p.gate}`;
  const detail = detailOf(e);
  return detail ? `${e.type} · ${detail}` : e.type;
}

/** Everything the left list shows for one node (C.1, W14 · A): its rounds, its
 *  escalation turns, and the node-level events outside every round. */
export function nodeRounds(node: string, events: KraftEvent[], sessions: WorkerSession[]): NodeRounds {
  const own = (groupByNode(events).find((g) => g.node === node)?.events ?? []).slice().reverse();
  const nodeSessions = sessions.filter((s) => s.node_id === node);
  const rounds = roundsOf(own, nodeSessions);
  const entries: TimelineEntry[] = rounds.map((r) => ({ kind: "round", at: r.startedAt, round: r }));
  const escalationSessions = nodeSessions
    .filter((x) => x.hook_point === "escalation")
    .sort((a, b) => a.created_at.localeCompare(b.created_at));
  const byThread = new Map<number, WorkerSession[]>();
  for (const s of escalationSessions) {
    const list = byThread.get(s.thread) ?? [];
    list.push(s);
    byThread.set(s.thread, list);
  }
  for (const [thread, threadSessions] of [...byThread.entries()].sort((a, b) => a[0] - b[0])) {
    entries.push({ kind: "escalationThread", at: threadSessions[0].created_at, thread, sessions: threadSessions });
  }
  // W14 · A: a round is one row, so what happened inside it is not; and a
  // node_started / node_completed within a minute of a round's edge is that edge.
  // The end is exclusive: a gate requested as the round's last session exits is the node's, not the round's.
  const inRound = (at: string) => rounds.some((r) => at >= r.startedAt && (!r.endedAt || at < r.endedAt));
  const nearEdge = (at: string) =>
    rounds.some((r) => [r.startedAt, r.endedAt].some((edge) => edge && Math.abs(Date.parse(at) - Date.parse(edge)) <= 60_000));
  let progress: Extract<TimelineEntry, { kind: "event" }> | null = null;
  for (const e of own) {
    if (inRound(e.created_at) || ((e.type === "node_started" || e.type === "node_completed") && nearEdge(e.created_at))) {
      progress = null;
      continue;
    }
    if (SESSION_TYPES.has(e.type) || ROUND_TYPES.has(e.type) || sessionOf(e)) {
      if (e.type !== "task_progress") progress = null;
      continue;
    }
    if (e.type === "task_progress") {
      // A run of task_progress is one row (D.2's rule, applied here too).
      if (progress) {
        progress.last = e;
        continue;
      }
      progress = { kind: "event", at: e.created_at, event: e, label: "task_progress", last: e };
      entries.push(progress);
      continue;
    }
    progress = null;
    entries.push({ kind: "event", at: e.created_at, event: e, label: eventLabel(e) });
  }
  // Oldest first; at the same instant an event sits before the round it opens,
  // so newest-first lists show the round above its `node_started`.
  entries.sort((a, b) => a.at.localeCompare(b.at) || (a.kind === "event" ? -1 : 0) - (b.kind === "event" ? -1 : 0));
  const times = own.map((e) => e.created_at);
  return { node, rounds, entries, events: own, startedAt: times[0] ?? null, endedAt: times[times.length - 1] ?? null };
}

/** `Task 3 → 6 of 6` for a run of task_progress events. */
export function taskRunLabel(first: KraftEvent, last: KraftEvent = first): string {
  const a = first.payload.task as number | undefined;
  const b = last.payload.task as number | undefined;
  const total = last.payload.total as number | undefined;
  if (a == null || b == null) return "task_progress";
  return `Task ${a === b ? a : `${a} → ${b}`}${total != null ? ` of ${total}` : ""}`;
}

/** The Timeline selection (`tnode` in the URL, C.4): `session:<id>`,
 *  `round:<node>:<n>`, `event:<seq>`. W11's `node:<seq>` (one event) and a bare
 *  `node` (a node's whole stream, a card's "see Timeline") still parse. */
export type TimelineSelection =
  | { kind: "session"; id: string }
  | { kind: "round"; node: string; n: number }
  | { kind: "event"; seq: number; node: string | null }
  | { kind: "node"; node: string };

export function parseTimelineSelection(sel: string | null | undefined): TimelineSelection | null {
  if (!sel) return null;
  if (sel.startsWith("session:")) return { kind: "session", id: sel.slice("session:".length) };
  const round = sel.match(/^round:(.+):(\d+)$/);
  if (round) return { kind: "round", node: round[1], n: Number(round[2]) };
  const event = sel.match(/^event:(\d+)$/);
  if (event) return { kind: "event", seq: Number(event[1]), node: null };
  const legacy = sel.match(/^(.+):(\d+)$/);
  if (legacy) return { kind: "event", seq: Number(legacy[2]), node: legacy[1] };
  return { kind: "node", node: sel };
}

/** Which node a selection is about, for the right pane and the pill. */
export function selectionNode(
  sel: TimelineSelection | null,
  events: KraftEvent[],
  sessions: WorkerSession[],
): string | null {
  if (!sel) return null;
  if (sel.kind === "round" || sel.kind === "node") return sel.node;
  if (sel.kind === "session") return sessions.find((s) => s.id === sel.id)?.node_id ?? null;
  if (sel.node) return sel.node;
  const hit = groupByNode(events).find((g) => g.events.some((e) => e.seq === sel.seq));
  return hit?.node ?? null;
}
