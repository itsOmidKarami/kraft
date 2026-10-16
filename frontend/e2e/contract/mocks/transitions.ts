import { FAILURE_STOP_KINDS, LIVE_SESSION_STATUSES, WORK_ITEM_TRAITS, type DisplayStatus, type EventType, type SessionStatus, type StopKind, type WorkItemStatus } from "../../../src/types/vocab.generated";
import { documentsFor, hex, type ItemBundle, type Scenario } from "./fixtures";
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

/** The escalation thread whose turn is pending or running, if one is. */
const liveThread = (item: any) => (item.escalation_threads ?? []).find((t: any) => LIVE_SESSION_STATUSES.includes(t.status as SessionStatus));
const escalationLive = (item: any) => liveThread(item) !== undefined;
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
  const settle = (item: any) => {
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
    const live = b.sessions.filter((s) => s.node_id === b.item.current_node_id && LIVE_SESSION_STATUSES.includes(s.status));
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
  const running = (it: any): Result | null => {
    const live = liveThread(it);
    return live ? no(409, `an escalation turn (${live.session_id}) is already running`) : null;
  };
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
    /** POST /work-items/{id}/resume: a paused item, or one stopped on a question; a steer starts a new attempt. */
    resume(id: string, steer: string | null = null): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { b, it } = x;
      if (!(it.status === "paused" || (it.status === "needs_human" && it.stop?.kind === "question"))) return no(409, notPaused(it));
      const busy = running(it);
      if (busy) return busy;
      run(it);
      it.pending_steer_context = null;
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
      append(id, "work_item_resumed", { steer });
      settle(it);
      return ok({ id, node_id: it.current_node_id, steer, steered: [] });
    },
    /** POST /work-items/{id}/retry: a stopped item, on its current node. */
    retry(id: string, steer: string | null = null): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { b, it } = x;
      if (!nodeIds(it).includes(it.current_node_id)) return no(409, "work item has no current node to retry");
      if (!ADMITS.retry.includes(it.status))
        return no(409, it.status === "paused" ? "work item is paused, not stopped: resume it instead, or skip what it would run" : "work item is not stopped");
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
      const busy = running(it);
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
      const busy = running(it);
      if (busy) return busy;
      const threads: any[] = (it.escalation_threads = [...(it.escalation_threads ?? [])]);
      const last = threads.at(-1);
      const thread = newThread || !last ? (last?.thread ?? 0) + 1 : last.thread;
      const turn = newThread || !last ? 1 : last.turns + 1;
      const from = b.sessions.at(-1) ?? {};
      const sid = sessionId();
      b.sessions.push({ ...from, id: sid, work_item_id: id, node_id: it.current_node_id, hook_point: "escalation", status: "running", attempt: 1, round: 0, thread, created_at: now(), started_at: now(), exited_at: null, tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null, session_summary_ref: null });
      b.logs[sid] = [...(b.logs[from.id] ?? [])];
      const at = { thread, session_id: sid, turns: turn, started_at: last && !newThread ? last.started_at : now(), ended_at: null, status: "running" };
      if (newThread || !last) threads.push(at); else threads[threads.length - 1] = at;
      append(id, "escalation_message", { session_id: sid, message: message.trim(), auto: false, thread, turn }, it.current_node_id);
      settle(it);
      return ok({ id, status: "escalating" });
    },
    /** POST /work-items/{id}/escalate/stop (`lifecycle.stop_escalation`): the running turn ends, the item stays as it stopped. */
    stopEscalation(id: string): Result {
      const x = find(id);
      if (refused(x)) return x;
      const { b, it } = x;
      const live = liveThread(it);
      if (!live) return no(409, "no escalation turn is running");
      const at = now();
      const s = b.sessions.find((y) => y.id === live.session_id);
      if (s) Object.assign(s, { status: "paused", exited_at: at });
      it.escalation_threads = it.escalation_threads.map((y: any) => (y === live ? { ...y, status: "paused", ended_at: at } : y));
      append(id, "worker_session_paused", { session_id: live.session_id });
      settle(it);
      return ok({ id, session_id: live.session_id, status: "paused" });
    },
    /** A `request_changes` review with no gate pending (`review._request_changes_now`): a rewind to `target`, then a
     *  stopped item retries there, a running one at that node reruns it, anything else waits for it. */
    requestChanges(id: string, target: string, note: string): Result {
      const x = find(id, true);
      if (refused(x)) return x;
      const { it } = x;
      const action = it.status === "needs_human" ? "retried" : WORK_ITEM_TRAITS[it.status as WorkItemStatus]?.running && target === it.current_node_id ? "rerun" : "queued";
      if (action === "retried") {
        const r = nodeIds(it).includes(target) ? null : no(409, "work item has no current node to retry");
        if (r) return r;
      }
      append(id, "rewind_requested", { review_id: "r2", target, note });
      if (action === "retried") {
        it.current_node_id = target;
        const r = t.retry(id);
        if (r.status !== 200) return r;
      }
      if (action === "rerun") { t.pause(id); t.resume(id); }
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
      return ok({ id, status: b.item.status, bead_id: null }, 201);
    },
  };
  const move = (id: string, out: string[], into: string[], where: "front" | "back") => {
    const at = out.indexOf(id);
    if (at < 0) return;
    out.splice(at, 1);
    if (where === "front") into.unshift(id); else into.push(id);
  };
  return t;
}
