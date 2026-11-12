import type { EventType } from "../../../src/types/vocab.generated";
import { commandLog, hex, logLines, LONG_DESCRIPTION, t, type ItemBundle, type Variant } from "./fixtures";
import { chainOf, frozenOf, type DefNode } from "./ngChain";
import { RESUME_PROMPT } from "./poller";
import { agentUsage, agentWall, awaitPolls, drain, DOCS_RED, fixCycle, judge, measured, nodeBeats, OPEN, ownFailure, redOf, REPAIR, REVIEW, REVIEWED, TESTS, testPass, type Found, type Open, type Writer } from "./nodeRecipe";
import { isAgentSession, RUNS, taskRun } from "./taskKinds";
import { escalationThreads, stepOf, stopTaskOf, threadSummary } from "./transitions";
import { isLate, mrOf, planOf, type Content } from "./ngContent";

/**
 * Items for the item page (ux2-W5) and the board (ngBoard.ts): the shipped default chain (ngChain.ts) in every
 * scenario the item page draws, plus paused, each standing where a real item in that state stands, with the sessions
 * and events a real server writes on the way there. The item page's live in the scenario's bundles only (the detail
 * route answers them), never in the board list. Times are T0-based; the ng-item cases fix the page clock at NG_NOW so
 * elapsed and "ago" read the same every run.
 */
export const NG_SCENARIOS = [
  "running", "needs-you", "needs-gate", "escalated", "capped", "done", "cancelled", "archived",
  "failed", "waiting", "conflict", "mr-closed", "waiting-ci", "paused", "queued", "blocked",
] as const;
export type NgScenario = (typeof NG_SCENARIOS)[number];
import { NG_NOW } from "./clock";
export { NG_NOW };

/** The node each scenario stands on, on the default chain; null: past its last node. */
const AT: Record<NgScenario, string | null> = {
  running: "verification", "needs-you": "verification", escalated: "verification", waiting: "verification",
  paused: "verification", queued: "verification", blocked: "verification", cancelled: "verification",
  capped: "implementation", "waiting-ci": "merge_request_feedback", "mr-closed": "merge_request_feedback",
  "needs-gate": "final_review", failed: "draft_merge_request", conflict: "draft_merge_request", done: null, archived: null,
};
/** The default chain's node order: where a node another chain lacks would stand. */
const ORDER = chainOf("default")!.nodes.map((n) => n.id);
const STUCK = "stuck: fix attempt 2 in node verification changed nothing, so measuring again would see the same code. The repair ended done and gave no concerns.";
/** What every automatic escalation turn is sent (`gates._AUTO_ESCALATE_MESSAGE`); a person's turn says its own. */
export const AUTO_ESCALATE_MESSAGE = "(Auto-escalated: no one has looked at this yet. Diagnose why it stopped and fix it if you can; if you're not confident, stop and say so instead of guessing.)";
/** `mr_poller.REASON`: the stop when the forge says the item's merge request was closed unmerged. */
export const MR_CLOSED_REASON = "the merge request was closed on the forge without merging";
/** What the person said at the final review, each thread's comment. The sample's are about its own lines; a board row
 *  with a hunk of its own (kraft-cb59's new cache module, ngContent.ts) is reviewed on that: a store of a key already
 *  cached left it where it was, first in line for eviction (since fixed, the hunk's `move_to_end` in `store`), how the
 *  bound counts, and which entry eviction drops. */
const REVIEW_OF_SAMPLE = { must: "`commits` can be empty: `max()` of an empty generator raises", question: "why no size bound?", local: "cache has no size bound" };
const REVIEW_OF_HUNK = {
  must: "Storing a key that is already cached leaves it where it was in the OrderedDict, so a hot entry stored again is the first one evicted: move it to the end on every `store`.",
  question: "`size` counts entries, not bytes: at 1,536 floats an embedding, 4096 of them is about 25 MB. Is a count the bound we want, or should it be bytes?",
  local: "`lookup` moves a hit to the end, so eviction drops the least recently used entry, not the oldest stored: say which in the class docstring.",
};
/** The review's summary, as the note of the rejection it sent (`store.render_note`). */
const REVIEW_SUMMARY = "Three things before this ships.";
const NOTE_HEAD = 'Review threads to address (reply to each with `kraft item reply <thread-id> --claim fixed|answered --body "..."`):';
/** `store.render_note`: the review's summary, then each thread not resolved when it was submitted, where it sits
 *  (`render.thread_where`, a new-side range `path:+5 to +7`), the lines it quotes and the first comment's body. */
export function renderNote(threads: any[], summary: string): string {
  const where = (t: any) => `${t.file_path}:+${t.start_line}${t.start_line === t.end_line ? "" : ` to +${t.end_line}`}`;
  const blocks = threads.filter((t) => t.state !== "resolved").map((t) =>
    [`[${t.id}] ${where(t)}${t.label ? ` (${t.label})` : ""}`, ...(t.quote ? t.quote.split("\n") : []).map((l: string) => `    | ${l}`), t.comments[0].body].join("\n"));
  return [summary, ...(blocks.length ? [NOTE_HEAD, blocks.join("\n\n")] : [])].join("\n\n");
}
/** `mr`: the merge request its draft opens, what its forge tasks print and its stop names; `content`: what the item is
 *  about, which its plan's tasks, its question and its review threads' lines are written from (a board row's own,
 *  ngContent.ts). Without them, the sample's. `spend`: a capped item stopped by its spend cap instead of a time cap. */
export function buildNgItem(sc: NgScenario, seed: number, variant: Variant, template = "default", mr = mrOf("/Users/dev/code/kraft-plugins", 142), content?: Content, o: { spend?: boolean } = {}): ItemBundle {
  const long = variant === "long";
  const id = hex(seed);
  const chain = chainOf(template)!;
  const FROZEN = frozenOf(template);
  const nodes: DefNode[] = chain.nodes;
  const has = (node: string) => nodes.some((n) => n.id === node);
  // A scenario's node this chain lacks: the chain's last node before it (docs_only has no verification, so implementation).
  const want = AT[sc];
  const currentNode = want === null ? nodes.at(-1)!.id : has(want) ? want : nodes.filter((n) => ORDER.indexOf(n.id) < ORDER.indexOf(want)).at(-1)!.id;
  const cur = want === null ? nodes.length : nodes.findIndex((n) => n.id === currentNode);
  const sessions: any[] = [];
  const events: any[] = [];
  // The file verification's round adds, whose test its first round fails (nodeRecipe.ts `failedTest`).
  const late = content?.files.find(isLate)?.[0] ?? null;
  const logs: Record<string, any[]> = {};
  let seq = 0;
  // Minutes from T0. A time cap's two hours of implementation start the capped item before T0, so it stops before NG_NOW.
  const start = sc === "capped" ? -15 : 0;
  let m = start;
  const ev = (type: EventType, payload: Record<string, unknown> = {}, dm = 0.02, column?: string) => {
    m += dm;
    // The column: a gate event names its node in `gate` only (`store.gates`); one whose payload names none, its own.
    const node = column ?? payload.node_id ?? payload.node ?? payload.gate;
    events.push({ seq: ++seq, work_item_id: id, type, payload, node_id: typeof node === "string" ? node : null, created_at: t(m) });
  };

  /** `store.create_session`, then an agent's launch: the session row and the events that say so. It runs until `close`. */
  const open = (path: string, node: string, o: Open = {}) => {
    const n = sessions.length;
    const sid = hex(seed * 100 + n + 1);
    const agent = !o.noAgent && isAgentSession(FROZEN, path);
    const run = agent || o.noAgent ? null : o.run ?? RUNS[taskRun(FROZEN, path) ?? ""] ?? null;
    const ids = { session_id: sid, node_id: node, hook_point: path, round: o.round ?? 0, attempt: o.attempt ?? 1, thread: o.thread ?? 1 };
    const s: any = {
      id: sid, work_item_id: id, node_id: node, hook_point: path, status: o.status ?? "running", attempt: ids.attempt, round: ids.round, thread: ids.thread,
      created_at: t(m), started_at: t(m + 0.01), exited_at: null, tokens_in: null, tokens_out: null, cost_usd: null, wall_ms: null,
      // A skipped task (no agent started) carries no head (`dispatch.skipped_session`).
      model: null, command: run?.command ?? null, head_sha: o.noAgent ? null : hex(seed + 400 + n).slice(0, 40), session_summary_ref: null,
    };
    sessions.push(s);
    ev("worker_session_created", ids, 0);
    if (path.endsWith(".auto_review")) ev("gate_auto_review_started", { gate: node, session_id: sid }, 0);
    if (agent) ev("worker_session_started", { ...ids, pid: 41_000 + n }, 0.01);
    logs[sid] = agent ? logLines(sid, long ? 300 : 12, long) : o.noAgent ? [] : commandLog(run, s.status, path.split(".").at(-1), mr.url);
    return Object.assign(s, { _agent: agent, _run: run, _noAgent: !!o.noAgent });
  };
  /** `store.session_exited`: `status` after its wall time, with the usage an agent reported. */
  const close = (s: any, status = "done", wall_ms?: number, advance = true) => {
    const n = sessions.indexOf(s);
    const wall = wall_ms ?? (s._agent ? agentWall(s.hook_point, n) : s._noAgent ? 1 : s._run?.wall_ms ?? 1_000);
    if (advance) m += wall / 60_000;
    Object.assign(s, { status, exited_at: t(m), wall_ms: wall });
    let usage = {};
    if (s._agent) {
      usage = agentUsage(s.hook_point, n);
      Object.assign(s, usage);
      if (status === "done" || status === "done_with_concerns") s.session_summary_ref = `items/${id}/sessions/${s.id}/out/summary.md`;
    } else if (s._noAgent) {
      logs[s.id] = [{ n: 1, t: t(0.1), src: "stdout", text: "no bot feedback collected; no agent started" }];
    } else {
      logs[s.id] = ownFailure(commandLog(s._run, status, s.hook_point.split(".").at(-1), mr.url), late);
    }
    ev("worker_session_exited", { session_id: s.id, status, wall_ms: wall, ...usage }, 0);
    return s;
  };
  /** A session that ran to `status` and the time it took. */
  const ran = (path: string, node: string, status = "done", o: Open = {}) => close(open(path, node, o), status);

  const title = long
    ? "Design the caching layer for document search so large repositories stop re-embedding every candidate document on every single query, keyed by content hash"
    : "Design the caching layer for document search";
  const item: any = {
    id, title, repo: "/Users/dev/code/kraft-plugins",
    description: long ? LONG_DESCRIPTION : "Search re-embeds every candidate document on every query, so large repos take 2–4 s per search. Cache embeddings keyed by document content hash, invalidate them when reindex sees a new hash, and keep the cache bounded. The public search API stays unchanged.",
    status: "active", chain_template: template, one_off: null,
    chain_definition: { template_id: template, nodes },
    effective_chain: { template_id: template, nodes: JSON.parse(JSON.stringify(nodes)) },
    materialized_chain: FROZEN,
    current_node_id: currentNode, bead_id: "kraft-cb59",
    created_at: t(start), updated_at: t(start),
    auto_gate: true, pending_gate: null, gate_artifact: null, steerable: true,
    // The long variant overrides its chain's fix loop where it has one (docs_only has none).
    node_overrides: long && has("verification") ? { verification: { attempts: 4 } } : {}, node_overrides_count: long && has("verification") ? 1 : 0,
    agent_overrides: null,
    budget_cap: { cap_usd: 20, source: long ? "item" : "policy", spent_usd: 0, daily: { spent_usd: 18.2, cap_usd: 50 } },
    worktree_path: `/Users/dev/.kraft/run/worktrees/${id}`, head_sha: hex(seed + 77).slice(0, 40), review_head: null,
    repos: [], attachments: [], mr_ref: null, progress: null, rate_limit: null,
    escalation_threads: [], concerns: [], deferred_findings: [],
    display_status: "running", stop: null,
    summary: { nodes_done: 0, nodes_total: nodes.length, gates_passed: 0, step: null },
  };

  /** The recipe's writes (nodeRecipe.ts), as this item records them: each stamped by its own clock `m`. */
  const W: Writer = {
    id, sessions, events, frozen: FROZEN, repo: item.repo, plan: planOf(FROZEN, content), late,
    get head() { return item.head_sha; },
    ev, open, close,
    enter: (node, dm) => ev("node_started", { node_id: node }, dm),
    wait: (s, node, next, wall) => {
      // `store.session_exited` stamps a waiting exit as it does any other.
      Object.assign(s, { status: "waiting", exited_at: t(m), wall_ms: wall });
      ev("worker_session_exited", { session_id: s.id, status: "waiting", wall_ms: wall }, 0);
      ev("work_item_waiting", { node_id: node, retry_at: next }, 0);
      logs[s.id] = commandLog(s._run, "waiting", s.hook_point.split(".").at(-1), mr.url);
    },
    at: (dm) => t(m + dm),
    clock: (dm) => { m += dm; },
    sha: (k) => hex(seed + 300 + k).slice(0, 40),
    requestGate: (gate) => ev("gate_requested", { gate, node_id: gate }, 0.02),
    selfApprove: (gate) => ev("gate_approved", { gate, by: "kraft" }, 0.02),
    // `forge.run`: `{number, url}`, the node in the column only.
    mrOpened: (node) => { ev("mr_opened", { ...mr }, 0.02, node); item.mr_ref = { ...mr }; },
    complete: (node) => ev("node_completed", { node_id: node }, 0.02),
    skipped: () => false,
    passed: () => false,
  };
  /** A node the walk ran to its end; at a gate, the person's approval too (`chain_revision_approval` passes itself). */
  const runNode = (n: DefNode) => {
    drain(nodeBeats(W, n));
    if (n.kind === "gate" && n.id !== "chain_revision_approval") {
      ev("gate_approved", { gate: n.id, by: "human" }, 3);
      ev("node_completed", { node_id: n.id }, 0.02);
    }
  };

  ev("work_item_created", { title, repo: item.repo, chain_template: template }, 0);
  ev("chain_loaded", {}, 0.02);
  for (let i = 0; i < cur; i++) runNode(nodes[i]);
  if (cur < nodes.length) ev("node_started", { node_id: currentNode }, 0.2);

  const stop = (kind: string, task: string | null, more: Record<string, unknown> = {}) => ({ kind, node: currentNode, task, attempt: 1, resume_at: null, reason: null, facts: {}, ...more });
  /** The task the item is at: on verification, its code review in round 1, after the first round's tests failed and a fix
   *  cycle mended them; on a chain with no verification (docs_only), its implementation. Left `status`, still open. No
   *  judge yet: it is due at a measurement after a repair, and round 1's is still being taken. */
  const atTask = (status: string) => {
    if (currentNode !== "verification") {
      const s = open(`${currentNode}.main.implement`, currentNode);
      item.summary.step = null;
      return Object.assign(s, { status });
    }
    // Round 0: the tests step failed, so the review step after it never ran.
    drain(testPass(W, 0, true));
    measured(W, 0, [redOf(late)], [TESTS]);
    drain(fixCycle(W, 1, true));
    drain(testPass(W, 1));
    const s = open(REVIEW, "verification", { round: 1 });
    item.summary.step = { index: 2, count: 2 };
    return Object.assign(s, { status });
  };
  /** `store.pause_work_item` stopping `s`: paused at that moment, a `worker_session_paused` and no exit. Nothing writes a
   *  paused row's `wall_ms`: it is read off its stamps (`_common.session_wall_ms`). */
  const pausedAt = (s: any, dm: number) => {
    ev("worker_session_paused", { session_id: s.id }, dm);
    s.exited_at = t(m);
    s.wall_ms = Date.parse(s.exited_at) - Date.parse(s.started_at);
  };
  /** An escalation turn on the node it stopped at: its message, then its session, born and launched as `escalate.dispatch`
   *  launches every turn's. An automatic one (`gates.auto_escalate_stuck`) is sent `AUTO_ESCALATE_MESSAGE`. */
  const turn = (n: number, message: string, auto: boolean, status = "running") => {
    const node = currentNode;
    const s = open("escalation", node, { attempt: n });
    events.splice(events.length - 2, 0, { seq: 0, work_item_id: id, type: "escalation_message", payload: { session_id: s.id, node_id: node, message, auto, thread: 1, turn: n }, node_id: node, created_at: events.at(-2).created_at });
    events.forEach((e, k) => { e.seq = k + 1; });
    seq = events.length;
    s.status = status;
    return s;
  };

  // Where the threads sit: a board row's on the lines its own hunk shows (ngContent.ts `hunks`), which they quote.
  const [cache, own] = Object.entries(content?.hunks ?? {})[0] ?? [];
  const at = (k: number) => own!.at + own!.rows.slice(0, k).filter((r) => r[0] !== "-").length;
  const quote = (from: number, to: number) => own!.rows.filter((r) => r[0] !== "-").slice(from - own!.at, to - own!.at + 1).join("\n");
  // A board row's threads are about its own hunk (kraft-cb59's store, its bound and its lookup, ngContent.ts), the sample's about its own.
  const [must, bound, local] = own ? [[at(19), at(20)], [at(7), at(8)], [at(11), at(16)]] : [[12, 14], [30, 30], [40, 44]];
  const said = own ? REVIEW_OF_HUNK : REVIEW_OF_SAMPLE;
  // Filed on the first attempt's head, which the review was submitted on too (both `rev-parse HEAD`); the must-fix was
  // resolved once the rework mended it, after the review sent it back.
  const head = "4d1e2f3";
  const threads = sc === "needs-gate" ? [
    { id: "th1", work_item_id: id, gate: null, node_id: "local_review", file_path: cache ?? "search/cache.py", side: "new", start_line: local[0], end_line: local[1], ...(own && { quote: quote(local[0], local[1]) }), anchor_sha: head, label: "question", state: "open", resolved_at: null, created_at: t(90), comments: [{ id: "c1", author: "you", body: said.local, created_at: t(90) }] },
    { id: "th-r1-must", work_item_id: id, gate: "final_review", node_id: "implementation", file_path: cache ?? "search/cache.py", side: "new", start_line: must[0], end_line: must[1], quote: own ? quote(must[0], must[1]) : " def newest(commits):\n+    latest = max(c.ts for c in commits)\n+    return self._cache[latest]", anchor_sha: head, label: "must_fix", state: "resolved", resolved_at: t(95.1), created_at: t(92), comments: [{ id: "c-r1-must", author: "you", body: said.must, created_at: t(92) }] },
    { id: "th-r1-q", work_item_id: id, gate: "final_review", node_id: "implementation", file_path: cache ?? "search/reindex.py", side: "new", start_line: bound[0], end_line: bound[1], quote: own ? quote(bound[0], bound[1]) : "+        self._cache[key] = value", anchor_sha: head, label: "question", state: "open", resolved_at: null, created_at: t(93), comments: [{ id: "c-r1-q", author: "you", body: said.question, created_at: t(93) }] },
  ] : [];
  const reviews = sc === "needs-gate" ? [{ id: "rv1", gate: "final_review", outcome: "request_changes", summary: REVIEW_SUMMARY, head_sha: head, submitted_at: t(95) }] : [];

  switch (sc) {
    case "running": case "paused": case "cancelled": case "queued": case "blocked": {
      const held = sc !== "running";
      const cr = atTask("running");
      // `store.pause_work_item`: the pause, naming the sessions it stops, then each of them.
      if (held && sc !== "cancelled") { ev("pause_requested", { sessions: [cr.id] }, 1); pausedAt(cr, 0); cr.status = "paused"; }
      if (sc === "queued") {
        // Resumed with every slot busy: `store.queue_work_item` holds the start until one frees.
        ev("work_item_queued", { verb: "resume", from: "paused" }, 20);
        item.status = "queued"; item.display_status = "queued";
        item.queued = { verb: "resume", since: t(m), storage: null };
      }
      if (sc === "blocked") {
        // Resumed behind an unfinished item (`store.block_work_item`): it starts when that one completes.
        // shortcut: the dependency is the board's never-started row by its seed (ngBoard.ts ROWS[9]); move this with that row.
        const on = { id: hex(1309), title: "Spike: stream logs over SSE", status: "paused", met: false };
        ev("work_item_blocked", { verb: "resume", from: "paused", on: [on.id] }, 20);
        item.status = "blocked"; item.display_status = "blocked";
        item.dependencies = [on];
      }
      if (sc === "paused") {
        item.status = "paused"; item.display_status = "paused";
        item.pending_steer_context = long ? "Look at the invalidation path first: the race only shows when reindex and search overlap." : null;
      }
      if (sc === "cancelled") {
        // `store.end_work_item`: the running sessions stopped first, then the cancel, then its own status write.
        pausedAt(cr, 1); cr.status = "paused";
        ev("work_item_cancelled", { reason: long ? "Superseded by kraft-cb61, which moves the cache into the index service; this item's branch stays for reference." : "", node_id: currentNode }, 0);
        ev("work_item_abandoned", {}, 0);
        item.status = "abandoned"; item.display_status = "cancelled";
      }
      break;
    }
    case "escalated": {
      // Round 0's tests failed and the first repair mended them, but round 1's docs check failed: a new finding, so the judge
      // (due now a repair has run) lets the loop go on. The second repair changed nothing, so the walk stops rather than
      // measure the same code again (`walk._changed_nothing_reason`), a stuck stop that auto-escalation answers.
      drain(testPass(W, 0, true));
      measured(W, 0, [redOf(late)], [TESTS]);
      drain(fixCycle(W, 1, true));
      drain(testPass(W, 1, false, true));
      measured(W, 1, [DOCS_RED], [TESTS]);
      drain(judge(W, 1, [DOCS_RED], "the repair fixed the failing test; the docs check is a new, small finding one more repair should reach"));
      drain(fixCycle(W, 2, false));
      ev("work_item_needs_human", { node_id: "verification", reason: STUCK, kind: "stuck", stuck: true }, 0.05);
      // The automatic escalation agent's turn running on it: what `display_status` calls escalated.
      turn(1, AUTO_ESCALATE_MESSAGE, true);
      item.status = "needs_human";
      item.stop = stop("stuck", REPAIR, { attempt: 2, reason: STUCK });
      item.display_status = "escalated";
      break;
    }
    case "needs-you": {
      // Round 1's code review asked a question instead of reviewing (`needs_context`): a measuring task's question stops
      // the node for a person before any fix cycle (`walk`), and no escalation answers a question (`gates.stuck_stop`).
      const cr = atTask("running");
      const q = content?.question ?? "The race needs a reindex API change the plan froze. Allow the change, or accept the finding and move on?";
      close(cr, "needs_context");
      // A failed task with no findings file is a blind failure (`findings.from_blind_failure`): its log's last lines.
      const blind: Found = { finding: { severity: "critical", message: `${REVIEW} failed\n- ${q}`, file: null, line: null, source_plugin: REVIEW }, print: "9a0d3e7f1c52b846" };
      measured(W, 1, [blind]);
      ev("work_item_needs_human", { node_id: "verification", reason: `needs_context: ${q}`, kind: "question", session_id: cr.id }, 0.05);
      item.status = "needs_human"; item.display_status = "needs_you"; item.needs_context_question = q;
      item.stop = stop("question", REVIEW, { reason: `needs_context: ${q}` });
      break;
    }
    case "capped": {
      if (o.spend) {
        // The implementer ran its whole plan to its end, the node completed and verification's tests passed; the spend
        // cap stops the item before its code review, an agent's task, launches (`dispatch.over_budget`), on verification
        // (`stops.stop_for_budget` starts no session). Its stop's task and attempt, and its step, are read off the node's
        // latest session, the tests' (`board._stop_task_and_attempt`). The cap's words and numbers are ngBoard.ts `spendCapped`'s.
        const s = open("implementation.main.implement", "implementation");
        W.plan.forEach((title, k) => ev("plan_progress", { node_id: "implementation", task: k + 1, total: W.plan.length, title }, 1.5));
        m -= 1.5 * W.plan.length;
        close(s);
        ev("node_completed", { node_id: "implementation" }, 0.02);
        ev("node_started", { node_id: "verification" }, 0.2);
        drain(testPass(W, 0));
        ev("work_item_needs_human", { node_id: "verification", reason: null, kind: "budget" }, 0.05);
        item.current_node_id = "verification";
        item.status = "needs_human"; item.display_status = "needs_you";
        const { task, attempt } = stopTaskOf(sessions, "verification");
        item.stop = { ...stop("budget", task), node: "verification", attempt };
        item.step = stepOf(nodes, "verification", task);
        item.summary.step = { index: item.step.index, count: item.step.count };
        // `dispatch` recorded the code review's step before the cap check refused its launch (`set_current_step`).
        item.current_step = nodes.find((n) => n.id === "verification")!.steps!.findIndex((g) => g.includes(REVIEW));
        break;
      }
      // The shipped implementer's own time cap, 120 minutes, stops its running session (`session_exited`, capped_out) and
      // names what it hit (`time_cap_reached`), then the item stops with that reason. A task's cap is the chain's: no item
      // override raises it, so the stop names no `limit` (`caps.item_limit`).
      const s = open("implementation.main.implement", "implementation");
      // Its plan's first three tasks (ngContent.ts `planOf`), out of all of them.
      const plan = W.plan;
      plan.slice(0, 3).forEach((title, k) => ev("plan_progress", { node_id: "implementation", task: k + 1, total: plan.length, title }, 25));
      m -= 75;
      close(s, "capped_out", 120 * 60_000);
      const reason = "`implementation.main.implement` hit its time cap of 120 minutes";
      ev("time_cap_reached", { scope: "implementation.main.implement", field: "time_cap_minutes", minutes: 120, reason, node_id: "implementation", task: s.hook_point, session_id: s.id }, 0);
      ev("work_item_needs_human", { node_id: "implementation", reason, kind: "cap" }, 0.05);
      item.status = "needs_human"; item.display_status = "needs_you";
      item.stop = stop("cap", "implementation.main.implement", { reason });
      break;
    }
    case "waiting": {
      // Rate limited three times on the task it is at: the poller relaunched it twice (`rate_limit_retry`, its counter at 2),
      // and the third attempt waits for its limit to reset. A launch the provider refused reports no usage.
      let s = atTask("running");
      for (let k = 0; k < 3; k++) {
        m += 0.3;
        Object.assign(s, { status: "rate_limited", exited_at: t(m), wall_ms: 18_000 });
        ev("worker_session_exited", { session_id: s.id, status: "rate_limited", wall_ms: 18_000 }, 0);
        ev("work_item_rate_limited", { node_id: currentNode, retry_at: k < 2 ? t(m + 2) : t(133.7) }, 0);
        if (k === 2) break;
        ev("work_item_retried", { node_id: currentNode, loop: null, steer: RESUME_PROMPT, escalated: false, seeded: false }, 2);
        ev("node_started", { node_id: currentNode }, 0);
        s = open(s.hook_point, currentNode, { attempt: s.attempt + 1, round: s.round });
      }
      item.status = "rate_limited"; item.display_status = "waiting"; item.retry_at = t(133.7);
      item.rate_limit = { count: 2, cap: 5 };
      // No reason: a rate limit writes no stop event (`board._stop_payload`). Its facts are the poller's count, and a
      // fallback only where the task names one, which no shipped task does (`board._rate_limit_facts`).
      item.stop = stop("rate_limit", s.hook_point, { attempt: s.attempt, resume_at: t(133.7), facts: { retries: { ...item.rate_limit } } });
      break;
    }
    case "waiting-ci": {
      const s = drain(awaitPolls(W, "merge_request_feedback", "merge_request_feedback.ci.await_ci", "mr.ci", 3, false, t(133)));
      item.summary.step = { index: 1, count: 3 };
      item.status = "waiting"; item.display_status = "waiting"; item.retry_at = t(133);
      // An external wait's `work_item_waiting` says no reason (`board._stop_payload`).
      item.stop = stop("wait", s.hook_point, { resume_at: t(133) });
      break;
    }
    case "mr-closed": {
      // `mr_poller` saw the merge request closed while CI was awaited: a stop, and no `mr_closed` event (only Kraft's
      // own cancel writes one).
      const s = drain(awaitPolls(W, "merge_request_feedback", "merge_request_feedback.ci.await_ci", "mr.ci", 2, false));
      const facts = { ref: mr.number, url: mr.url };
      ev("work_item_needs_human", { node_id: "merge_request_feedback", reason: MR_CLOSED_REASON, kind: "mr_closed", facts }, 2);
      item.status = "needs_human"; item.display_status = "needs_you";
      item.stop = stop("mr_closed", s.hook_point, { reason: MR_CLOSED_REASON, facts });
      break;
    }
    case "needs-gate": {
      // Round 1, a review that sent it back: asked at t(91), rejected at t(95), the instant the review was submitted.
      m = Math.max(m, 90.9);
      ev("gate_requested", { node_id: "final_review", gate: "final_review" }, 0.1);
      ran("final_review.auto_review", "final_review");
      ev("gate_auto_review_skipped", { gate: "final_review", reason: "passed", note: REVIEWED }, 0.02);
      m = Math.max(m, 94.98);
      // The review's note (`submit_review`): its summary and the threads open as it was submitted.
      const sent = reviews[0].submitted_at;
      const asSent = threads.map((x) => ({ ...x, state: x.resolved_at && x.resolved_at <= sent ? "resolved" : "open" }));
      ev("gate_rejected", { gate: "final_review", node: "implementation", by: "human", note: renderNote(asSent, REVIEW_SUMMARY), verdict: "reject" }, 0.02);
      ev("gate_requested", { node_id: "final_review", gate: "final_review" }, 0.2);
      item.status = "needs_human"; item.display_status = "needs_you"; item.pending_gate = "final_review";
      // A re-review (ux2-W8): two attempts at the gate, and a review submitted on the first.
      item.attempts = [{ n: 1, sha: "4d1e2f3", base_sha: "1a2b3c4", at: t(3) }, { n: 2, sha: "9c8d7e6", base_sha: "1a2b3c4", at: t(1.1) }];
      item.last_review_sha = "4d1e2f3";
      item.head_sha = "9c8d7e6";
      item.review_head = "9c8d7e6";
      const ids = nodes.map((n) => n.id);
      item.fix_target = { gate: "final_review", node: "implementation", then: ids.slice(ids.indexOf("implementation") + 1, ids.indexOf("final_review")), round: { n: 2, max: 3 } };
      item.gate_artifact = `items/${id}/docs/review_brief.md`;
      item.stop = stop("gate", null);
      break;
    }
    case "failed": {
      // The forge refused the token (403) on opening the draft. The node has no fix loop and no failure handler, so one
      // failed attempt is the task's failure (`walk`): its reason names the task and the first line of its log, its facts
      // the cause (`walk._forge_cause`). A failed task is in the stuck set, so an automatic escalation turn followed;
      // it has ended, a token being nothing its agent can fix, and the item stays failed for a person.
      ran("draft_merge_request.rebase.rebase", "draft_merge_request");
      const s = ran(OPEN, "draft_merge_request", "failed");
      const reason = `task failed in node draft_merge_request: open [forge] — ${logs[s.id][0].text}`;
      const facts = { cause: "forge_auth" };
      ev("work_item_needs_human", { node_id: "draft_merge_request", reason, kind: "failed", session_id: s.id, stuck: true, facts }, 0.05);
      const e = close(turn(1, AUTO_ESCALATE_MESSAGE, true));
      e.session_summary_ref = threadSummary(id, 1);
      item.status = "needs_human"; item.display_status = "failed";
      item.summary.step = { index: 2, count: 2 };
      item.stop = stop("failed", OPEN, { reason, facts });
      break;
    }
    case "conflict": {
      // Paused while it opened the draft; resumed after main moved. The resume rebased the branch first
      // (`lifecycle.refresh_base`, draft_merge_request not rebasing itself) and hit a conflict. No shipped node declares
      // `on_conflict`, so the walk stops for a person (`walk._door_conflict`) with git's words, the rebase aborted:
      // nothing is left unmerged in the worktree.
      ran("draft_merge_request.rebase.rebase", "draft_merge_request");
      const s = open(OPEN, "draft_merge_request");
      ev("pause_requested", { sessions: [s.id] }, 0.05);
      pausedAt(s, 0); s.status = "paused";
      ev("work_item_resumed", { steer: null }, 25);
      const reason = `git rebase failed for ${item.worktree_path}: Auto-merging search/cache.py\nCONFLICT (content): Merge conflict in search/cache.py\nAuto-merging search/reindex.py\nCONFLICT (content): Merge conflict in search/reindex.py\nerror: could not apply 4d1e2f3... Cache embeddings by content hash`;
      const facts = { resolved: [], unresolved: [] };
      ev("work_item_needs_human", { node_id: "draft_merge_request", reason, kind: "conflict", facts }, 0.05);
      item.status = "needs_human"; item.display_status = "needs_you";
      item.summary.step = { index: 2, count: 2 };
      item.stop = stop("conflict", OPEN, { reason, facts });
      break;
    }
    case "done": case "archived": {
      ev("work_item_completed", {}, 0.05);
      item.status = "completed"; item.display_status = sc === "archived" ? "archived" : "done";
      if (sc === "archived") { item.archived_at = t(m + 30); item.archived_by = "you"; }
      break;
    }
  }
  for (const s of sessions) { delete s._agent; delete s._run; delete s._noAgent; }
  item.escalation_threads = escalationThreads(sessions);
  item.updated_at = t(m);
  const done = new Set(events.filter((e) => e.type === "node_completed").map((e) => e.payload.node_id));
  item.summary = { ...item.summary, nodes_done: nodes.filter((n) => done.has(n.id)).length, gates_passed: nodes.filter((n) => n.kind === "gate" && done.has(n.id)).length };
  const byNode = [...new Set(sessions.map((s) => s.node_id))].map((node) => {
    const ss = sessions.filter((s) => s.node_id === node);
    const sum = (k: string) => ss.reduce((a, s) => a + (s[k] ?? 0), 0);
    return { node, tokens_in: sum("tokens_in"), tokens_cache_write: sum("tokens_cache_write"), tokens_cache_read: sum("tokens_cache_read"), tokens_out: sum("tokens_out"), cost_usd: Math.round(sum("cost_usd") * 10_000) / 10_000, cost_complete: true, split_complete: true, wall_ms: sum("wall_ms"), sessions: ss.length, rounds: 1, capped_out: 0 };
  });
  const total = byNode.reduce((a, r) => ({ tokens_in: a.tokens_in + r.tokens_in, tokens_cache_write: a.tokens_cache_write + r.tokens_cache_write, tokens_cache_read: a.tokens_cache_read + r.tokens_cache_read, tokens_out: a.tokens_out + r.tokens_out, cost_usd: a.cost_usd + r.cost_usd, wall_ms: a.wall_ms + r.wall_ms }), { tokens_in: 0, tokens_cache_write: 0, tokens_cache_read: 0, tokens_out: 0, cost_usd: 0, wall_ms: 0 });
  total.cost_usd = Math.round(total.cost_usd * 10_000) / 10_000;
  item.usage = { total: { ...total, cost_complete: true, split_complete: true, sessions: sessions.length, rounds: 1, capped_out: 0 }, by_node: byNode };
  item.budget_cap.spent_usd = total.cost_usd;
  return { item, sessions, events, logs, threads, reviews };
}

/** An item filed paused that never ran: its chain, no node, no session, no event, nothing spent. What a new item is made
 *  from (transitions.ts `create`) and the board's never-started row; each adds its own `work_item_created`. */
export function buildNeverStarted(seed: number, variant: Variant, template = "default"): ItemBundle {
  const b = buildNgItem("paused", seed, variant, template);
  const it = b.item;
  Object.assign(it, {
    current_node_id: null, pending_steer_context: null, stop: null, escalation_threads: [], concerns: [], deferred_findings: [],
    // No worktree until the walk first enters a node (`board`'s `worktree_exists`), so no diff to show.
    worktree_path: null, worktree_exists: false, head_sha: null, branch: null, mr_ref: null, progress: null, step: null, rate_limit: null, retry_at: null,
    attempts: [], last_review_sha: null, fix_target: null, needs_context_question: null,
    budget_cap: { ...it.budget_cap, source: "policy", spent_usd: 0 },
    usage: { total: { tokens_in: 0, tokens_cache_write: 0, tokens_cache_read: 0, tokens_out: 0, cost_usd: 0, cost_complete: true, split_complete: true, wall_ms: 0, sessions: 0, rounds: 0, capped_out: 0 }, by_node: [] },
    summary: { nodes_done: 0, nodes_total: it.chain_definition.nodes.length, gates_passed: 0, step: null },
  });
  return { item: it, sessions: [], events: [], logs: {}, threads: [], beads: [] };
}

/** ux2-W8: the review threads a needs-gate item starts with, on the first two
 *  files of its comparison (`compareFor`), at lines its diff has: a must-fix
 *  still open, a question an agent answered and claimed, a resolved nit, and
 *  a draft of yours with a suggested change. */
export function ngThreads(wid: string, files: string[]): any[] {
  const [a, b = a] = files;
  let n = 0;
  const c = (author: string, body: string, o: Record<string, unknown> = {}) => ({ id: `c${wid.slice(0, 6)}${n++}`, review_id: author === "you" && o.draft ? null : "r1", author, attempt: null, body, suggestion: null, claim: null, created_at: "2026-09-13T09:40:00.000Z", draft: false, ...o });
  const t = (id: string, file_path: string, side: string, start: number, end: number, label: string | null, state: string, comments: any[], draft = false) => {
    for (const x of comments) x.thread_id = id;
    return { id, work_item_id: wid, gate: "final_review", node_id: "implementation", file_path, side, start_line: start, end_line: end, anchor_sha: "9c8d7e6", label, state, resolved_at: state === "resolved" ? "2026-09-13T09:55:00.000Z" : null, created_at: "2026-09-13T09:40:00.000Z", comments, draft };
  };
  return [
    t("th-must", a, "new", 4, 4, "must_fix", "open", [c("you", "`commits` can be empty: `max()` of an empty generator raises. The `default=0` covers it, but say so in the docstring.")]),
    t("th-q", a, "new", 7, 8, "question", "claimed", [
      c("you", "Why take the max of both signals instead of trusting the commit subjects?"),
      c("implementation", "Reports lag the commits by one cycle, so the max is the safe reading; `test_progress` covers both orders.", { attempt: 2, claim: "answered" }),
    ]),
    t("th-nit", b, "old", 2, 2, "nit", "resolved", [c("you", "`Optional` is unused after this change.")]),
    t("th-draft", a, "new", 5, 5, null, "open", [c("you", "Tighten the docstring:", { draft: true, suggestion: { start_line: 5, end_line: 5, replacement: '    """The highest task any report or commit subject names."""' } })], true),
  ];
}
