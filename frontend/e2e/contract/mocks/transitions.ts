import { FAILURE_STOP_KINDS, LIVE_SESSION_STATUSES, WORK_ITEM_TRAITS, type DisplayStatus, type EventType, type SessionStatus, type StopKind, type WorkItemStatus } from "../../../src/types/vocab.generated";
import { documentsFor, hex, logLines, type ItemBundle, type Scenario } from "./fixtures";
import { ngDryRun } from "./ngBoard";
import { buildNgItem } from "./ngItems";

/**
 * The work item's transitions, as the server makes them: what each door changes, the event row it appends
 * (with the next `seq`), and the refusal it answers, in the words of `kraft.api.routes.lifecycle` and
 * `kraft.api.routes.gates`. Transport-free: mockApi.ts's routes and its bulk door call these, and so can
 * anything else that moves an item (the demo's live motion). Every transition ends in `settle`, which sets
 * `display_status` from `displayStatus`: nothing else assigns it. Every event appended is also handed to
 * `onEvent`, which the demo forwards to its event socket.
 *
 * State is the scenario itself: an item is its bundle in `S.bundles`, and `S.ngBoard` / `S.ngArchived`
 * hold the board's ids in order, so a list row read after an action is that action's item.
 */

export type Ev = { seq: number; work_item_id: string; type: EventType; payload: Record<string, unknown>; node_id: string | null; created_at: string };
/** What a door answers: the HTTP status and its body (a refusal's is `{ detail }`). */
export type Result = { status: number; body: any };

const ok = (body: unknown, status = 200): Result => ({ status, body });
const no = (status: number, detail: string): Result => ({ status, body: { detail } });

/** `kraft.api.routes.board.display_status`: archived, else the status's own badge, else (needs_human) the
 *  stop's: a pending gate needs you, a live escalation turn is escalated, a failure kind failed. */
export function displayStatus(item: any): DisplayStatus {
  if (item.archived_at) return "archived";
  const shown = WORK_ITEM_TRAITS[item.status as WorkItemStatus]?.display;
  if (shown) return shown;
  if (item.pending_gate) return "needs_you";
  if (escalationLive(item)) return "escalated";
  return FAILURE_STOP_KINDS.includes(item.stop?.kind as StopKind) ? "failed" : "needs_you";
}

/** The item's escalation thread whose turn is pending or running, if one is. */
const liveThread = (item: any) => (item.escalation_threads ?? []).find((t: any) => LIVE_SESSION_STATUSES.includes(t.status as SessionStatus));
const escalationLive = (item: any) => liveThread(item) !== undefined;

/** `store.escalation_threads`: one entry per thread of the item's escalation sessions, oldest first, each read off
 *  its sessions (its last one's id, status and exit), so a thread can never outlive the session it stands for. */
export function escalationThreads(sessions: any[]) {
  const by = new Map<number, any[]>();
  for (const s of sessions) if (s.hook_point === "escalation") by.set(s.thread ?? 1, [...(by.get(s.thread ?? 1) ?? []), s]);
  return [...by.entries()].sort(([a], [b]) => a - b).map(([thread, ss]) => ({
    thread, session_id: ss.at(-1).id, turns: ss.length, started_at: ss[0].created_at, ended_at: ss.at(-1).exited_at ?? null, status: ss.at(-1).status,
  }));
}
/** `store.pending_rewind`: the newest `rewind_requested`, unless its target's `node_started` or its own
 *  `rewind_cancelled` came after it. The server's walk writes that `node_started` as soon as the resume or retry
 *  that honoured the rewind starts there; the doors here are not a walk, so the honouring `work_item_resumed` or
 *  `work_item_retried` stands in for it, and spends the rewind too. Resume and retry start there; the demo's walk
 *  (motion.ts) goes back there once the node it stands on completes, as `walk.run_once` does. */
export function pendingRewind(b: ItemBundle): { target: string; note: string | null } | null {
  const at = b.events.map((e) => e.type).lastIndexOf("rewind_requested");
  if (at < 0) return null;
  const p = b.events[at].payload as { review_id: string; target: string; note: string | null };
  const spent = b.events.slice(at + 1).some((e) =>
    (e.type === "node_started" && e.payload.node_id === p.target) || (e.type === "rewind_cancelled" && e.payload.review_id === p.review_id) ||
    e.type === "work_item_resumed" || e.type === "work_item_retried");
  return spent ? null : p;
}
const ENDED = (s: string) => WORK_ITEM_TRAITS[s as WorkItemStatus]?.ended ?? false;
/** `kraft.vocab.work_item.TRAITS[...].admits_status`, the status half of each door (never exported to TS). */
const ADMITS: Record<string, readonly string[]> = {
  pause: ["active", "rate_limited", "waiting", "queued", "blocked"],
  retry: ["needs_human"],
  skip: ["active", "needs_human", "paused", "waiting"],
  escalate: ["needs_human", "paused"],
  abandon: ["needs_human", "completed", "paused", "abandoned", "rate_limited", "waiting", "queued", "blocked"],
  archive: ["completed", "abandoned"],
};

/** The board's row of an item: the item, its `stop` without facts, task or attempt (W4's list). */
export function listRow(item: any) {
  const s = item.stop;
  return { ...item, stop: s ? { kind: s.kind, node: s.node, resume_at: s.resume_at, reason: s.reason } : null };
}

/** `lifecycle.not_paused`: what resume says to an item that is not paused. */
function notPaused(item: any): string {
  if (WORK_ITEM_TRAITS[item.status as WorkItemStatus]?.running) return `work item is ${item.status}: it is running, so pause it first`;
  if (item.status === "needs_human" && item.pending_gate)
    return `work item is waiting at its ${item.pending_gate} gate, not paused: approve or reject it (kraft item approve ${item.id}, or kraft item reject ${item.id} --note ...)`;
  if (item.status === "needs_human") return `work item is needs_human, not paused: it stopped, so retry it instead (kraft item retry ${item.id}, which takes --steer too)`;
  return `work item is ${item.status}, not paused`;
}

const py = (s: string) => `'${s}'`;
const usd = (v: number | null) => (v === null ? "no cap" : `$${Number(v.toPrecision(6))}`);
const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
/** A sum of dollars without the float's tail ($2.4100000001). */
const dollars = (v: number) => Math.round(v * 10_000) / 10_000;

/** What a new item is made from: a sample item before it ran, with nothing on it that says it did. Built on its
 *  own, not taken from the scenario, so no edit to a sample item reaches it and the empty scenario has one too. */
function template(): ItemBundle {
  const b = buildNgItem("paused", 9999, "default");
  const it = b.item;
  Object.assign(it, {
    current_node_id: null, pending_steer_context: null, stop: null, escalation_threads: [], concerns: [], deferred_findings: [],
    worktree_path: null, head_sha: null, branch: null, mr_ref: null, progress: null, rate_limit: null, retry_at: null,
    attempts: [], last_review_sha: null, fix_target: null, needs_context_question: null,
    budget_cap: { ...it.budget_cap, source: "policy", spent_usd: 0 },
    usage: { total: { tokens_in: 0, tokens_out: 0, cost_usd: 0, cost_complete: true, wall_ms: 0, sessions: 0, rounds: 0, capped_out: 0 }, by_node: [] },
  });
  return { item: it, sessions: [], events: [], logs: {}, threads: [], beads: [] };
}

export interface TransitionOptions {
  /** The instant an event or an `updated_at` records. */
  now?: () => string;
  /** The item's unresolved must-fix review threads, which approve refuses on (`store.open_must_fix`). */
  mustFix?: (id: string) => string[];
  /** Told of every event appended, after it is on its bundle. */
  onEvent?: (e: Ev) => void;
}

export type Transitions = ReturnType<typeof transitions>;

export function transitions(S: Scenario, o: TransitionOptions = {}) {
  const now = o.now ?? (() => new Date().toISOString());
  let seq = Math.max(4242, ...Object.values(S.bundles).flatMap((b) => b.events.map((e) => e.seq)));
  let made = 0;
  let sessions = 0;
  const blank = template();

  /** Append an event with the next seq. `node_id` defaults as `events.append` defaults it: the payload's own `node_id` or `node`. */
  const append = (id: string, type: EventType, payload: Record<string, unknown> = {}, node_id?: string | null): Ev => {
    const named = payload.node_id ?? payload.node;
    const e: Ev = { seq: ++seq, work_item_id: id, type, payload, node_id: node_id !== undefined ? node_id : typeof named === "string" ? named : null, created_at: now() };
    S.bundles[id].events.push(e as ItemBundle["events"][number]);
    o.onEvent?.(e);
    return e;
  };
  /** A session id no session in the scenario has. */
  const sessionId = () => {
    const taken = new Set(Object.values(S.bundles).flatMap((b) => b.sessions.map((s) => s.id)));
    let sid = hex(900_000 + sessions++);
    while (taken.has(sid)) sid = hex(900_000 + sessions++);
    return sid;
  };
  /** The item's escalation threads as its sessions say they are now; its live one, if any. */
  const liveTurn = (b: ItemBundle) => liveThread({ escalation_threads: escalationThreads(b.sessions) });
  const settle = (item: any) => {
    item.escalation_threads = escalationThreads(S.bundles[item.id].sessions);
    item.display_status = displayStatus(item);
    item.updated_at = now();
    return item;
  };
  /** The item, or `deps._work_item_row`'s 404; `live` also refuses an ended one (`deps._live_work_item_row`). */
  const find = (id: string, live = false): { b: ItemBundle; it: any } | Result => {
    const b = S.bundles[id];
    if (!b) return no(404, "unknown work item");
    if (live && ENDED(b.item.status)) return no(409, `work item is ${b.item.status}; its chain does not run again`);
    return { b, it: b.item };
  };
  const refused = (x: { b: ItemBundle; it: any } | Result): x is Result => "status" in x;
  const nodeIds = (it: any): string[] => (it.chain_definition?.nodes ?? []).map((n: any) => n.id);
  /** Mark the current node's live sessions paused (`store.pause_work_item`); the caller narrates them with `paused`. */
  const stopSessions = (b: ItemBundle): string[] => {
    // `store.running_sessions_for_node`: the current node's, and an escalation turn's on any node.
    const live = b.sessions.filter((s) => (s.node_id === b.item.current_node_id || s.hook_point === "escalation") && LIVE_SESSION_STATUSES.includes(s.status));
    for (const s of live) Object.assign(s, { status: "paused", exited_at: now() });
    return live.map((s) => s.id);
  };
  /** One `worker_session_paused` per session `stopSessions` stopped. */
  const paused = (b: ItemBundle, ids: string[]) => { for (const sid of ids) append(b.item.id, "worker_session_paused", { session_id: sid }); };
  /** `gates._gate_or_404` (a gate node of the item's chain), then the pending check both gate doors make. */
  const gateRefusal = (it: any, gate: string): Result | null => {
    if (!(it.chain_definition?.nodes ?? []).some((n: any) => n.id === gate && n.kind === "gate")) return no(404, `unknown gate ${py(gate)}`);
    return it.pending_gate === gate ? null : no(409, `gate ${py(gate)} is not pending`);
  };
  const running = (b: ItemBundle): Result | null => {
    const live = liveTurn(b);
    return live ? no(409, `an escalation turn (${live.session_id}) is already running`) : null;
  };
  /** `lifecycle._stop_live_sessions`: what a person's decision stops first. At a gate (`pauseItem`) as
   *  `store.pause_work_item` narrates it; for a retry each turn as `store.stop_escalation_session` does. */
  const stopLive = (b: ItemBundle, pauseItem: boolean) => {
    const ids = stopSessions(b);
    if (ids.length && pauseItem) append(b.item.id, "pause_requested", { sessions: ids });
    paused(b, ids);
  };
  const joined = (...parts: (string | null | undefined)[]) => parts.filter(Boolean).join("\n\n") || null;
  const run = (it: any) => Object.assign(it, { status: "active", stop: null, pending_gate: null, gate_artifact: null, needs_context_question: null, retry_at: null });

  const t = {
    /** The newest event seq, as GET /work-items' `cursor` answers it. */
    cursor: () => seq,
    append,
    /** What the review doors refuse before anything (`review.submit_review`): an unknown or ended item, and at
     *  `gate`, a gate the chain does not have or one not pending. */
    reviewRefusal(id: string, gate?: string | null): Result | null {
      const x = find(id, true);
      if (refused(x)) return x;
      return gate ? gateRefusal(x.it, gate) : null;
    },
    /** POST /work-items/{id}/gates/{gate}/approve. */
    approve(id: string, gate: string): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { it } = x;
      const g = gateRefusal(it, gate);
      if (g) return g;
      const blocking = o.mustFix?.(id) ?? [];
      if (blocking.length) return no(409, `must-fix review threads are not resolved: ${blocking.join(", ")}`);
      stopLive(x.b, true);
      run(it);
      it.fix_target = null;
      append(id, "gate_approved", { gate, by: "human" }, gate);
      append(id, "node_completed", { node_id: gate });
      return ok(settle(it));
    },
    /** POST /work-items/{id}/gates/{gate}/reject: back to `node`, else the gate's `reject_to`, else the exec node
     *  before it, else the gate itself (`executor.reject_target`). */
    reject(id: string, gate: string, note: string, node?: string | null): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { it } = x;
      const g = gateRefusal(it, gate);
      if (g) return g;
      const nodes: any[] = it.chain_definition.nodes;
      const at = nodes.findIndex((n) => n.id === gate);
      const name: string | undefined = node || nodes[at].reject_to || undefined;
      const back = nodes.slice(0, at).reverse().find((n) => n.kind !== "gate");
      const to = name ? nodes.findIndex((n) => n.id === name) : back ? nodes.indexOf(back) : at;
      if (name && (to < 0 || to >= at)) return no(400, `cannot reject to ${py(name)}: not a node of this chain before ${py(gate)}`);
      const target = nodes[to].id;
      stopLive(x.b, true);
      run(it);
      Object.assign(it, { fix_target: null, attempts: [], current_node_id: target });
      append(id, "gate_rejected", { gate, note, node: target, by: "human", verdict: "reject" });
      return ok(settle(it));
    },
    /** POST /work-items/{id}/pause. */
    pause(id: string): Result {
      const x = find(id);
      if (refused(x)) return x;
      const { b, it } = x;
      if (!ADMITS.pause.includes(it.status)) return no(409, `work item is ${it.status}, not running`);
      Object.assign(it, { status: "paused", retry_at: null, stop: null });
      const ids = stopSessions(b);
      append(id, "pause_requested", { sessions: ids });
      paused(b, ids);
      settle(it);
      return ok({ id, paused_sessions: ids });
    },
    /** POST /work-items/{id}/resume: a paused item, or one stopped on a question. It starts where a pending rewind
     *  points (its note carried with the steer), else where it stopped, else, never started, at its first node.
     *  A steer starts a new attempt. */
    resume(id: string, steer: string | null = null): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { b, it } = x;
      if (!(it.status === "paused" || (it.status === "needs_human" && it.stop?.kind === "question"))) return no(409, notPaused(it));
      const busy = running(b);
      if (busy) return busy;
      const rewind = pendingRewind(b);
      // The answer names the node as the row read before the claim: none for a never-started item.
      const was = it.current_node_id ?? null;
      // `store.take_steer`: the request's steer replaces a queued one (`set_steer`); either is taken, and cleared.
      const taken = steer || it.pending_steer_context || null;
      run(it);
      it.pending_steer_context = null;
      it.current_node_id = rewind?.target ?? it.current_node_id ?? nodeIds(it)[0] ?? null;
      const carried = joined(rewind?.note, taken);
      if (steer) {
        // The next attempt of the node's own last task, waiting to start (an escalation turn is not one).
        const node = it.current_node_id;
        const last = b.sessions.filter((s) => s.node_id === node && s.hook_point !== "escalation").at(-1);
        b.sessions.push({
          id: sessionId(), work_item_id: id, node_id: node, hook_point: last?.hook_point ?? node, status: "pending",
          attempt: (last?.attempt ?? 0) + 1, round: last?.round ?? 0, thread: 1, model: last?.model ?? null, head_sha: last?.head_sha ?? null,
          created_at: now(), started_at: null, exited_at: null, tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null, session_summary_ref: null,
        });
      }
      append(id, "work_item_resumed", { steer: carried });
      settle(it);
      return ok({ id, node_id: was, steer: carried, steered: [] });
    },
    /** POST /work-items/{id}/retry: a stopped item, on its current node or where a pending rewind points (its note
     *  carried with the steer). A person's retry outranks a live escalation turn: it stops it first. */
    retry(id: string, given: string | null = null): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { b, it } = x;
      if (!nodeIds(it).includes(it.current_node_id)) return no(409, "work item has no current node to retry");
      if (!ADMITS.retry.includes(it.status))
        return no(409, it.status === "paused" ? "work item is paused, not stopped: resume it instead, or skip what it would run" : "work item is not stopped");
      const rewind = pendingRewind(b);
      const steer = joined(rewind?.note, given);
      // Only a live escalation turn is stopped: a person's retry outranks it.
      if (liveTurn(b)) stopLive(b, false);
      if (rewind) it.current_node_id = rewind.target;
      const attempt = Math.max(0, ...b.sessions.filter((s) => s.node_id === it.current_node_id).map((s) => s.attempt ?? 0)) + 1;
      run(it);
      append(id, "work_item_retried", { node_id: it.current_node_id, loop: null, steer, escalated: false, seeded: false });
      settle(it);
      return ok({ id, node_id: it.current_node_id, path: null, loop: null, steer, attempt });
    },
    /** POST /work-items/{id}/skip: the node it stands on (or its pending gate), or one task or step of it by path. */
    skip(id: string, path?: string | null, note: string | null = null): Result {
      const x = find(id);
      if (refused(x)) return x;
      const { b, it } = x;
      if (!ADMITS.skip.includes(it.status)) return no(409, `work item is ${it.status}, cannot skip`);
      const busy = running(b);
      if (busy) return busy;
      const node = it.pending_gate ?? it.current_node_id;
      if (!node || !nodeIds(it).includes(node)) return no(409, "work item has no current node to skip");
      if (path && !path.includes(".") && path !== node)
        return no(409, `path: ${py(path)} is not the node the item stands on (${py(it.current_node_id)}) or its pending gate`);
      const gate = it.pending_gate ?? null;
      const scope = path && path.includes(".") ? path : null;
      const ids = stopSessions(b);
      run(it);
      // As the store writes them: a node skip's event, then the sessions it stopped; a scope skip's after them.
      if (!scope) append(id, "node_skipped", { node_id: node, gate, note });
      paused(b, ids);
      if (scope) append(id, "scope_skipped", { path: scope, note });
      settle(it);
      return ok({ id, node_id: node, path: scope, note });
    },
    /** POST /work-items/{id}/cancel and /complete (`lifecycle._end_work_item`): a reason, then an end no door leads back from. */
    end(id: string, action: "cancel" | "complete", reason: string): Result {
      const x = find(id);
      if (refused(x)) return x;
      const { b, it } = x;
      if (!String(reason ?? "").trim()) return no(422, "reason: a terminal action needs a reason");
      if (ENDED(it.status)) return no(409, `work item is already ${it.status}`);
      paused(b, stopSessions(b));
      const node = it.current_node_id ?? null;
      Object.assign(it, { status: action === "cancel" ? "abandoned" : "completed", stop: null, pending_gate: null, gate_artifact: null, retry_at: null });
      append(id, action === "cancel" ? "work_item_cancelled" : "work_item_manually_completed", { reason: reason.trim(), node_id: node });
      append(id, action === "cancel" ? "work_item_abandoned" : "work_item_completed");
      return ok(settle(it));
    },
    /** POST /work-items/{id}/abandon: never while it runs. A second call reclaims again, with no second event. */
    abandon(id: string): Result {
      const x = find(id);
      if (refused(x)) return x;
      const { it } = x;
      if (!ADMITS.abandon.includes(it.status)) return no(409, "work item is active; pause it before abandoning");
      if (it.status !== "abandoned") {
        Object.assign(it, { status: "abandoned", stop: null, pending_gate: null, gate_artifact: null });
        append(id, "work_item_abandoned");
        settle(it);
      }
      return ok({ id, status: "abandoned", worktree_removed: true });
    },
    /** POST /work-items/{id}/archive: an ended item, onto the Archived list. Archiving one twice changes nothing. */
    archive(id: string): Result {
      const x = find(id);
      if (refused(x)) return x;
      const { it } = x;
      if (!ADMITS.archive.includes(it.status)) return no(409, "only a completed or abandoned item can be archived");
      if (it.archived_at) return ok({ id, archived_by: it.archived_by, worktree_removed: false });
      Object.assign(it, { archived_at: now(), archived_by: "you" });
      append(id, "work_item_archived", { by: "you" });
      move(id, S.ngBoard, S.ngArchived, "front");
      settle(it);
      return ok({ id, archived_by: "you", worktree_removed: true });
    },
    /** POST /work-items/{id}/restore: back under Done. */
    restore(id: string): Result {
      const x = find(id);
      if (refused(x)) return x;
      const { it } = x;
      if (!it.archived_at) return no(409, "work item is not archived");
      Object.assign(it, { archived_at: null, archived_by: null });
      append(id, "work_item_restored");
      move(id, S.ngArchived, S.ngBoard, "back");
      settle(it);
      return ok({ id, status: it.status });
    },
    /** POST /work-items/{id}/budget/raise: the cap a spend stop names, then a retry. */
    raiseBudget(id: string, budgetUsd: number | null): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { it } = x;
      if (!ADMITS.retry.includes(it.status)) return no(409, "work item is not stopped");
      if (it.stop?.kind !== "budget") return no(409, "work item was not stopped by a spend cap");
      const spent = it.budget_cap?.spent_usd ?? null;
      if (budgetUsd !== null && spent !== null && budgetUsd <= spent)
        return no(422, `the item has already spent ${usd(spent)}; a cap of ${usd(budgetUsd)} would stop it again at once: raise it above that, or to none`);
      if (!nodeIds(it).includes(it.current_node_id)) return no(409, "work item has no current node to retry");
      it.budget_cap = { ...(it.budget_cap ?? {}), cap_usd: budgetUsd, source: "item" };
      append(id, "budget_raised", { budget_usd: budgetUsd });
      return t.retry(id);
    },
    /** POST /work-items/{id}/escalate: a turn of an escalation agent, in the item's thread or a new one. */
    escalate(id: string, message: string, newThread = false): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { b, it } = x;
      if (!ADMITS.escalate.includes(it.status)) return no(409, "work item is not needs_human or paused");
      if (it.current_node_id == null) return no(409, "work item has not started");
      if (it.status === "needs_human" && it.stop?.kind === "budget")
        return no(409, `a spend cap stopped this item, and an escalation's agent would hit it too: raise it with \`kraft item raise-budget ${id} --usd N\` where that takes it (\`kraft view show ${id}\` names the cap), then escalate`);
      if (!String(message ?? "").trim()) return no(400, "message is required");
      const busy = running(b);
      if (busy) return busy;
      // The thread is its sessions: a turn joins the last one, or starts the next.
      const last = escalationThreads(b.sessions).at(-1);
      const thread = newThread || !last ? (last?.thread ?? 0) + 1 : last.thread;
      const turn = newThread || !last ? 1 : last.turns + 1;
      const from = b.sessions.at(-1) ?? {};
      const sid = sessionId();
      b.sessions.push({ ...from, id: sid, work_item_id: id, node_id: it.current_node_id, hook_point: "escalation", status: "running", attempt: 1, round: 0, thread, created_at: now(), started_at: now(), exited_at: null, tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null, session_summary_ref: null });
      b.logs[sid] = [...(b.logs[from.id] ?? [])];
      append(id, "escalation_message", { session_id: sid, message: message.trim(), auto: false, thread, turn }, it.current_node_id);
      settle(it);
      return ok({ id, status: "escalating" });
    },
    /** POST /work-items/{id}/escalate/stop (`lifecycle.stop_escalation`): the running turn ends, the item stays as it stopped. */
    stopEscalation(id: string): Result {
      const x = find(id);
      if (refused(x)) return x;
      const { b, it } = x;
      const live = liveTurn(b);
      if (!live) return no(409, "no escalation turn is running");
      // `store.stop_escalation_session`: the session, and with it its thread (`settle` reads them off the sessions).
      Object.assign(b.sessions.find((y) => y.id === live.session_id), { status: "paused", exited_at: now() });
      append(id, "worker_session_paused", { session_id: live.session_id });
      settle(it);
      return ok({ id, session_id: live.session_id, status: "paused" });
    },
    /** A `request_changes` review with no gate pending (`review._request_changes_now`): a rewind to `target`, then a
     *  stopped item retries there, a running one at that node reruns it, anything else waits for it: the next
     *  resume or retry starts where the rewind points. */
    requestChanges(id: string, target: string, note: string): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { it } = x;
      const at = nodeIds(it).indexOf(target);
      if (at < 0 || at > nodeIds(it).indexOf(it.current_node_id)) return no(400, `cannot request changes at ${py(target)}: not at or before the current node`);
      const action = it.status === "needs_human" ? "retried" : WORK_ITEM_TRAITS[it.status as WorkItemStatus]?.running && target === it.current_node_id ? "rerun" : "queued";
      append(id, "rewind_requested", { review_id: "r2", target, note }, target);
      const r = action === "retried" ? t.retry(id) : action === "rerun" ? (t.pause(id), t.resume(id)) : ok({});
      if (r.status !== 200) {
        // As `_request_changes_now` does: a refused action leaves no rewind pending.
        append(id, "rewind_cancelled", { review_id: "r2" });
        return r;
      }
      return ok({ action, target });
    },
    /** PATCH /work-items/{id}: a title or a description, each with its own event. Other fields are answered, not kept. */
    patch(id: string, body: Record<string, any>): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { it } = x;
      const fields = ["title", "description", "chain_template", "agent_overrides", "node_overrides", "policy", "attachments"];
      if (!fields.some((f) => body[f] != null) && !("budget_usd" in body))
        return no(422, "nothing to patch: send a title, description, chain, agent_overrides, node_overrides, policy, attachments, or budget_usd");
      if (body.title != null && !String(body.title).trim()) return no(422, "title cannot be empty");
      if (body.title != null) {
        it.title = body.title;
        append(id, "work_item_title_edited", { title: body.title });
      }
      if (body.description != null) {
        it.description = body.description || null;
        append(id, "work_item_description_edited", { description: body.description });
      }
      return ok({ ...settle(it), ...body, title: it.title, description: it.description });
    },
    /** POST /work-items (not a dry run): a new item, never started, on the chain the composer's dry run showed
     *  (`ngDryRun`: its template's nodes less what the attachments cover). Filed paused unless `autostart`, last on
     *  the board, with a document for each attachment. */
    create(body: Record<string, any>): Result {
      if (!String(body.title ?? "").trim()) return no(422, "title cannot be empty");
      const dry = ngDryRun(body);
      if (dry.status !== 200) return no(dry.status, (dry.body as { detail: string }).detail);
      let id = hex(9000 + made++);
      while (S.bundles[id]) id = hex(9000 + made++);
      const b = clone(blank);
      const chain = body.chain_template ?? "default";
      const nodes = clone((dry.body as { nodes: any[] }).nodes);
      const keep = new Set(nodes.map((n) => n.id));
      const frozen = JSON.parse(b.item.materialized_chain);
      frozen.chain.nodes = frozen.chain.nodes.filter((n: any) => keep.has(n.id));
      const at = now();
      Object.assign(b.item, {
        id, title: String(body.title).trim(), description: String(body.description ?? "").trim() || null, repo: body.repo ?? b.item.repo,
        chain_template: chain, chain_definition: { template_id: chain, nodes }, effective_chain: { template_id: chain, nodes: clone(nodes) },
        materialized_chain: JSON.stringify(frozen), bead_id: null, status: body.autostart ? "active" : "paused",
        // Started, it begins at its first node, as a resume of a never-started item does.
        current_node_id: body.autostart ? nodes[0]?.id ?? null : null,
        attachments: body.attachments ?? [], created_at: at,
        summary: { nodes_done: 0, nodes_total: nodes.length, gates_passed: 0, step: null },
      });
      S.bundles[id] = b;
      const kinds = new Set((body.attachments ?? []).map((a: { kind: string }) => `${a.kind}`));
      S.docs[id] = documentsFor(b.item, "default").filter((d) => d.attachment_kind && kinds.has(d.attachment_kind)).map((d, n) => ({ ...d, document_id: `${id.slice(0, 24)}${String(n).padStart(8, "0")}` }));
      S.ngBoard.push(id);
      append(id, "work_item_created", { title: b.item.title, repo: b.item.repo, chain_template: chain });
      if (body.attachments?.length) append(id, "work_item_attachments", { attachments: body.attachments });
      settle(b.item);
      // `work_items.create_work_item`'s two answers: filed paused, or started with the chain it runs.
      if (!body.autostart) return ok({ id, status: "paused" }, 201);
      const it = b.item;
      return ok({ id, bead_id: null, status: it.status, chain_definition: it.chain_definition, materialized_chain: it.materialized_chain, current_node_id: it.current_node_id }, 201);
    },

    /* The walk's writes: what the executor does to a running item, in the store's order and words. No route calls
       these; the demo's live motion (motion.ts) does. Each refuses an unknown or ended item, as the doors above do. */

    /** `store.enter_node`: the item stands on `node`, its step and plan progress cleared until a session reports. */
    enterNode(id: string, node: string): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { b, it } = x;
      if (!nodeIds(it).includes(node)) return no(409, `node ${py(node)} is not in this chain`);
      Object.assign(it, { current_node_id: node, step: null, progress: null });
      append(id, "node_started", { node_id: node });
      tally(b);
      return ok(settle(it));
    },
    /** `store.create_session`: a pending session of `node`'s task `hookPoint`, with a log to read. */
    createSession(id: string, node: string, hookPoint: string): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { b, it } = x;
      const attempt = b.sessions.filter((s) => s.node_id === node && s.hook_point === hookPoint).length + 1;
      const sid = sessionId();
      b.sessions.push({
        id: sid, work_item_id: id, node_id: node, hook_point: hookPoint, status: "pending", attempt, round: 0, thread: 1, model: null, head_sha: it.head_sha ?? null,
        created_at: now(), started_at: null, exited_at: null, tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null, session_summary_ref: null,
      });
      b.logs[sid] = logLines(sid, 12, false);
      append(id, "worker_session_created", { session_id: sid, node_id: node, hook_point: hookPoint, round: 0, attempt, thread: 1 });
      settle(it);
      return ok({ id, session_id: sid });
    },
    /** `store.session_running`: the session's process is up, and the item's step is the one its task is in. */
    sessionRunning(id: string, sid: string): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { b, it } = x;
      const s = b.sessions.find((y) => y.id === sid);
      if (!s) return no(404, `unknown session ${sid}`);
      if (s.status !== "pending") return no(409, `session ${sid} is ${s.status}, not pending`);
      Object.assign(s, { status: "running", started_at: now() });
      // `board._step_of`: only a node of more than one step has a step to name.
      const steps: string[][] = (it.chain_definition?.nodes ?? []).find((n: any) => n.id === s.node_id)?.steps ?? [];
      const at = steps.findIndex((g) => g.includes(s.hook_point));
      const [, name, task] = String(s.hook_point).split(".");
      it.step = steps.length > 1 && at >= 0 ? { index: at + 1, count: steps.length, name, task } : null;
      if (it.summary) it.summary = { ...it.summary, step: it.step ? { index: it.step.index, count: it.step.count } : null };
      append(id, "worker_session_started", { session_id: sid, node_id: s.node_id, hook_point: s.hook_point, round: s.round, attempt: s.attempt, thread: s.thread, pid: 40_000 + sessions });
      settle(it);
      return ok({ id, session_id: sid });
    },
    /** `store.session_exited`: the session done, with the usage it reported on the way out, added to the item's spend. */
    sessionExited(id: string, sid: string, usage: { model: string; tokens_in: number; tokens_out: number; cost_usd: number }): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { b, it } = x;
      const s = b.sessions.find((y) => y.id === sid);
      if (!s) return no(404, `unknown session ${sid}`);
      if (!LIVE_SESSION_STATUSES.includes(s.status)) return no(409, `session ${sid} is ${s.status}, not live`);
      const at = now();
      const wall_ms = Math.max(0, Date.parse(at) - Date.parse(s.started_at ?? s.created_at));
      Object.assign(s, { status: "done", exited_at: at, wall_ms, ...usage, tokens_cache_write: 0, tokens_cache_read: 0 });
      const u = it.usage;
      if (u) {
        const add = (r: any) => ({ ...r, tokens_in: r.tokens_in + usage.tokens_in, tokens_out: r.tokens_out + usage.tokens_out, cost_usd: dollars(r.cost_usd + usage.cost_usd), wall_ms: r.wall_ms + wall_ms, sessions: r.sessions + 1 });
        const row = u.by_node.find((r: any) => r.node === s.node_id);
        u.total = add(u.total);
        u.by_node = row ? u.by_node.map((r: any) => (r === row ? add(r) : r))
          : [...u.by_node, add({ node: s.node_id, tokens_in: 0, tokens_out: 0, cost_usd: 0, cost_complete: true, wall_ms: 0, sessions: 0, rounds: 1, capped_out: 0 })];
      }
      if (it.budget_cap) it.budget_cap = { ...it.budget_cap, spent_usd: dollars((it.budget_cap.spent_usd ?? 0) + usage.cost_usd) };
      append(id, "worker_session_exited", { session_id: sid, status: "done", wall_ms, model: usage.model, tokens_in: usage.tokens_in, tokens_cache_write: 0, tokens_cache_read: 0, tokens_out: usage.tokens_out, cost_usd: usage.cost_usd });
      settle(it);
      return ok({ id, session_id: sid });
    },
    /** `store.complete_node`: the node done; the item still stands on it until the walk enters the next. */
    completeNode(id: string, node: string): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { b, it } = x;
      if (!nodeIds(it).includes(node)) return no(409, `node ${py(node)} is not in this chain`);
      append(id, "node_completed", { node_id: node });
      tally(b);
      return ok(settle(it));
    },
    /** `store.request_gate`: the item stops at `gate` for a person, with the document the gate is about
     *  (`executor.gate_artifact`: `items/<id>/docs/<artifact>.md`). */
    requestGate(id: string, gate: string): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { it } = x;
      const node = (it.chain_definition?.nodes ?? []).find((n: any) => n.id === gate && n.kind === "gate");
      if (!node) return no(404, `unknown gate ${py(gate)}`);
      Object.assign(it, {
        status: "needs_human", current_node_id: gate, pending_gate: gate, gate_artifact: node.artifact ? `items/${id}/docs/${node.artifact}.md` : null,
        step: null, stop: { kind: "gate", node: gate, task: null, attempt: 1, resume_at: null, reason: null, facts: {} },
      });
      append(id, "gate_requested", { gate, node_id: gate });
      return ok(settle(it));
    },
    /** `store.mark_completed`: the chain ran to its end. */
    markCompleted(id: string): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { it } = x;
      Object.assign(it, { status: "completed", stop: null, pending_gate: null, gate_artifact: null, step: null, retry_at: null });
      append(id, "work_item_completed");
      return ok(settle(it));
    },
  };
  /** `board._summary`'s counts: the chain's nodes that completed, and how many of those are gates. */
  const tally = (b: ItemBundle) => {
    const it = b.item;
    if (!it.summary) return;
    const done = new Set(b.events.filter((e) => e.type === "node_completed").map((e) => e.payload.node_id));
    const nodes: any[] = it.chain_definition?.nodes ?? [];
    it.summary = { ...it.summary, nodes_done: nodes.filter((n) => done.has(n.id)).length, gates_passed: nodes.filter((n) => n.kind === "gate" && done.has(n.id)).length };
  };
  const move = (id: string, out: string[], into: string[], where: "front" | "back") => {
    const at = out.indexOf(id);
    if (at < 0) return;
    out.splice(at, 1);
    if (where === "front") into.unshift(id); else into.push(id);
  };
  return t;
}
