import type { EventType } from "../../../src/types/vocab.generated";
import { commandLog, hex, logLines, LONG_DESCRIPTION, t, type ItemBundle, type Variant } from "./fixtures";
import { chainOf, frozenOf, type DefNode } from "./ngChain";
import { RESUME_PROMPT } from "./poller";
import { agentRun, DOCS_SCOPE, isAgentSession, MODEL, RUNS, taskRun, type Run } from "./taskKinds";
import { escalationThreads } from "./transitions";

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
/** T0 + 130 minutes, as a literal: this module loads before fixtures.ts finishes (they import each other). */
export const NG_NOW = "2026-09-13T10:10:00.000Z";

/** The node each scenario stands on, on the default chain; null: past its last node. */
const AT: Record<NgScenario, string | null> = {
  running: "verification", "needs-you": "verification", escalated: "verification", waiting: "verification",
  paused: "verification", queued: "verification", blocked: "verification", cancelled: "verification",
  capped: "implementation", "waiting-ci": "merge_request_feedback", "mr-closed": "merge_request_feedback",
  "needs-gate": "final_review", failed: "draft_merge_request", conflict: "draft_merge_request", done: null, archived: null,
};
/** The default chain's node order: where a node another chain lacks would stand. */
const ORDER = chainOf("default")!.nodes.map((n) => n.id);
const MR = { number: 142, url: "https://github.com/acme/kraft-plugins/pull/142" };
/** A gate's reviewer, ready for a person: what it says on its way out. */
const REVIEWED = "The document covers what the gate asks: nothing in it needs another pass before you decide.";
const TESTS = "verification.tests.test_changed_scopes";
const REVIEW = "verification.review.code_review";
const STUCK = "stuck: fix attempt 2 in node verification changed nothing, so measuring again would see the same code. The repair ended done and gave no concerns.";

export function buildNgItem(sc: NgScenario, seed: number, variant: Variant, template = "default"): ItemBundle {
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
  const logs: Record<string, any[]> = {};
  let seq = 0;
  // Minutes from T0. A time cap's two hours of implementation start the capped item before T0, so it stops before NG_NOW.
  const start = sc === "capped" ? -15 : 0;
  let m = start;
  const ev = (type: EventType, payload: Record<string, unknown> = {}, dm = 0.02) => {
    m += dm;
    const node = payload.node_id ?? payload.node;
    events.push({ seq: ++seq, work_item_id: id, type, payload, node_id: typeof node === "string" ? node : null, created_at: t(m) });
  };

  type Open = { status?: string; round?: number; attempt?: number; thread?: number; run?: Run; noAgent?: boolean };
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
      model: null, command: run?.command ?? null, head_sha: hex(seed + 400 + n).slice(0, 40), session_summary_ref: null,
    };
    sessions.push(s);
    ev("worker_session_created", ids, 0);
    if (path.endsWith(".auto_review")) ev("gate_auto_review_started", { gate: node, node_id: node, session_id: sid }, 0);
    if (agent) ev("worker_session_started", { ...ids, pid: 41_000 + n }, 0.01);
    logs[sid] = agent ? logLines(sid, long ? 300 : 12, long) : o.noAgent ? [] : commandLog(run, s.status, path.split(".").at(-1));
    return Object.assign(s, { _agent: agent, _run: run, _noAgent: !!o.noAgent });
  };
  /** `store.session_exited`: `status` after its wall time, with the usage an agent reported. */
  const close = (s: any, status = "done", wall_ms?: number, advance = true) => {
    const n = sessions.indexOf(s);
    const recipe = s._agent ? agentRun(s.hook_point) : null;
    const wall = wall_ms ?? (recipe ? Math.round(recipe.wall_s * 1000 * (1 + (n % 3) * 0.07)) : s._noAgent ? 1 : s._run?.wall_ms ?? 1_000);
    if (advance) m += wall / 60_000;
    Object.assign(s, { status, exited_at: t(m), wall_ms: wall });
    let usage = {};
    if (recipe) {
      usage = { model: MODEL, tokens_in: 40 + ((n * 37) % 300), tokens_cache_write: 0, tokens_cache_read: 0, tokens_out: Math.round(recipe.tokens_out * (1 + (n % 4) * 0.05)), cost_usd: Math.round(recipe.cost_usd * (1 + (n % 4) * 0.05) * 10_000) / 10_000 };
      Object.assign(s, usage);
      if (status === "done" || status === "done_with_concerns") s.session_summary_ref = `.engineering/sessions/${s.id}.md`;
    } else if (s._noAgent) {
      logs[s.id] = [{ n: 1, t: t(0.1), src: "stdout", text: "no bot feedback collected; no agent started" }];
    } else {
      logs[s.id] = commandLog(s._run, status, s.hook_point.split(".").at(-1));
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
    budget_cap: { cap_usd: 25, source: long ? "item" : "policy", spent_usd: 0, daily: { spent_usd: 18.2, cap_usd: 50 } },
    worktree_path: `/Users/dev/.kraft/run/worktrees/${id}`, head_sha: hex(seed + 77).slice(0, 40), review_head: null,
    repos: [], attachments: [], mr_ref: null, progress: null, rate_limit: null,
    escalation_threads: [], concerns: [], deferred_findings: [],
    display_status: "running", stop: null,
    summary: { nodes_done: 0, nodes_total: nodes.length, gates_passed: 0, step: null },
  };

  /** A gate the walk passed: its reviewer ready for a person, then the person's approval. `chain_revision_approval`
   *  passes itself when the revision changed nothing (`chain_revision_unchanged`). */
  const passGate = (n: DefNode) => {
    ev("node_started", { node_id: n.id }, 0.2);
    if (n.id === "chain_revision_approval") {
      ev("chain_revision_unchanged", { gate: n.id, node_id: n.id, rationale: "The plan fits the chain as it stands: no node to add, drop or reorder." }, 0.02);
      ev("gate_approved", { gate: n.id, node_id: n.id, by: "kraft" }, 0.02);
    } else {
      ev("gate_requested", { gate: n.id, node_id: n.id }, 0.02);
      ran(`${n.id}.auto_review`, n.id);
      ev("gate_auto_review_skipped", { gate: n.id, node_id: n.id, reason: "passed", note: REVIEWED }, 0.02);
      ev("gate_approved", { gate: n.id, node_id: n.id, by: "human" }, 3);
    }
    ev("node_completed", { node_id: n.id }, 0.02);
  };
  /** One changed-test-scope pass: what it picked, then a session per scope command, the repo's tests failing when `red`. */
  const testPass = (round: number, red = false) => {
    ev("test_scopes_selected", { node_id: "verification", hook_point: TESTS, repository: null, round, commands: [RUNS["kraft.verify_changed_test_scopes"].command, DOCS_SCOPE.command], targeted: {}, full_reason: null }, 0.05);
    ran(TESTS, "verification", red ? "failed" : "done", { round });
    ran(TESTS, "verification", "done", { round, run: DOCS_SCOPE });
  };
  const measured = (findings: unknown[] = []) => ev("findings_measured", { node_id: "verification", findings, noop_hooks: [] }, 0.05);
  const RED = [{ severity: "critical", message: "test_cache_evicts_oldest fails: AssertionError", file: "tests/test_cache.py", line: 41, source_plugin: "pytest" }];
  /** A fix cycle: the repair, its judge's verdict, and whether the repair changed anything. */
  const fixCycle = (cycle: number, committed: boolean) => {
    ev("fix_cycle_started", { node_id: "verification", cycle, max_attempts: 2, failed_tasks: [TESTS] }, 0.05);
    ran("verification.fix_loop.main.repair", "verification", "done", { round: cycle, attempt: cycle });
    ran("verification.fix_loop.judge", "verification", "done", { round: cycle, attempt: cycle });
    ev("judge_verdict", { node_id: "verification", cycle, verdict: "continue", reasoning: "the eviction test still fails on the oldest entry; one more repair should reach it" }, 0.02);
    ev("fix_cycle_finished", { node_id: "verification", cycle, committed, head_sha: committed ? hex(seed + 300 + cycle).slice(0, 40) : null }, 0.02);
  };
  /** A forge wait that polls (`waits.py`): one session that goes `waiting` poll after poll, a fresh `node_started` each time
   *  its poller re-enters it; settled, it ends done. Unsettled, it is left waiting until `retryAt`. */
  const awaitPolls = (node: string, path: string, kind: string, polls: number, settled: boolean, retryAt?: string) => {
    const s = open(path, node);
    const each = (s._run?.wall_ms ?? 60_000) / polls;
    ev("external_wait_started", { node_id: node, task: path, kind, session_id: s.id }, 0.02);
    let wall = 0;
    for (let k = 1; k <= polls; k++) {
      if (k > 1) ev("node_started", { node_id: node }, 0.02);
      wall += each;
      m += each / 60_000;
      if (settled && k === polls) {
        ev("external_wait_observed", { node_id: node, task: path, observation: k, state: "settled" }, 0);
        ev("external_wait_ended", { node_id: node, task: path, outcome: "settled", observations: k, session_id: s.id }, 0);
        return close(s, "done", Math.round(wall), false);
      }
      const next = k === polls && retryAt ? retryAt : t(m + 2);
      ev("external_wait_observed", { node_id: node, task: path, observation: k, state: "pending", next_observation_at: next }, 0);
      Object.assign(s, { status: "waiting", wall_ms: Math.round(wall) });
      ev("worker_session_exited", { session_id: s.id, status: "waiting", wall_ms: Math.round(wall) }, 0);
      ev("work_item_waiting", { node_id: node, retry_at: next }, 0);
      if (k < polls) m += 2;
    }
    logs[s.id] = commandLog(s._run, "waiting", path.split(".").at(-1));
    return s;
  };

  /** A node the walk ran to its end. */
  const runNode = (n: DefNode) => {
    if (n.kind === "gate") return passGate(n);
    ev("node_started", { node_id: n.id }, 0.2);
    switch (n.id) {
      case "implementation": {
        const s = open("implementation.main.implement", n.id);
        const steps = ["Read the search path", "Key the cache by content hash", "Invalidate on reindex", "Bound the cache", "Tests", "Docs"];
        steps.forEach((title, k) => ev("plan_progress", { node_id: n.id, task: k + 1, total: steps.length, title }, 1.5));
        m -= 1.5 * steps.length;
        close(s);
        break;
      }
      case "verification":
        testPass(0);
        ran(REVIEW, n.id);
        measured();
        break;
      case "draft_merge_request":
        ran("draft_merge_request.rebase.rebase", n.id);
        ran("draft_merge_request.open.open", n.id);
        ev("branch_pushed", { node_id: n.id, sha: item.head_sha }, 0.02);
        ev("mr_opened", { node_id: n.id, ...MR }, 0.02);
        item.mr_ref = { ...MR };
        break;
      case "merge_request_feedback":
        awaitPolls(n.id, "merge_request_feedback.ci.await_ci", "mr.ci", 3, true);
        ran("merge_request_feedback.automated_review.await_review", n.id);
        ran("merge_request_feedback.review_feedback.triage", n.id, "done", { noAgent: true });
        break;
      case "post_merge_ci":
        awaitPolls(n.id, "post_merge_ci.main.await", "mr.post_merge_ci", 4, true);
        break;
      default:
        // A node of one task, or one per step: each runs in turn.
        for (const path of n.tasks as string[]) ran(path, n.id);
    }
    ev("node_completed", { node_id: n.id }, 0.02);
  };

  ev("work_item_created", { title, repo: item.repo, chain_template: template }, 0);
  ev("chain_loaded", { template_id: template, nodes: nodes.map((n) => n.id) }, 0.02);
  for (let i = 0; i < cur; i++) runNode(nodes[i]);
  if (cur < nodes.length) ev("node_started", { node_id: currentNode }, 0.2);

  const stop = (kind: string, task: string | null, more: Record<string, unknown> = {}) => ({ kind, node: currentNode, task, attempt: 1, resume_at: null, reason: null, facts: {}, ...more });
  /** The task the item is at: on verification, its code review in round 1, after the first round's tests failed and a fix
   *  cycle mended them; on a chain with no verification (docs_only), its implementation. Left `status`, still open. */
  const atTask = (status: string) => {
    if (currentNode !== "verification") {
      const s = open(`${currentNode}.main.implement`, currentNode);
      item.summary.step = null;
      return Object.assign(s, { status });
    }
    testPass(0, true);
    measured(RED);
    fixCycle(1, true);
    testPass(1);
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
  /** An escalation turn on verification: its message, then its session, born and launched as `escalate.dispatch` launches every turn's. */
  const turn = (n: number, message: string, auto: boolean, status = "running") => {
    const s = open("escalation", "verification", { attempt: n });
    events.splice(events.length - 2, 0, { seq: 0, work_item_id: id, type: "escalation_message", payload: { session_id: s.id, node_id: "verification", message, auto, thread: 1, turn: n }, node_id: "verification", created_at: events.at(-2).created_at });
    events.forEach((e, k) => { e.seq = k + 1; });
    seq = events.length;
    s.status = status;
    return s;
  };

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
      // Round 1's tests failed again, and the second repair changed nothing: the walk stops rather than measure the same code.
      testPass(0, true);
      measured(RED);
      fixCycle(1, true);
      testPass(1, true);
      measured(RED);
      ev("fix_cycle_started", { node_id: "verification", cycle: 2, max_attempts: 2, failed_tasks: [TESTS] }, 0.05);
      ran("verification.fix_loop.main.repair", "verification", "done", { round: 2, attempt: 2 });
      ev("fix_cycle_finished", { node_id: "verification", cycle: 2, committed: false, head_sha: null }, 0.02);
      ev("work_item_needs_human", { node_id: "verification", kind: "stuck", reason: STUCK }, 0.05);
      // An escalation agent's turn running on it: what `display_status` calls escalated.
      turn(1, "The repair keeps changing nothing: look at why test_cache_evicts_oldest still fails after two repairs.", true);
      item.status = "needs_human";
      item.stop = stop("stuck", TESTS, { attempt: 2, reason: STUCK });
      item.display_status = "escalated";
      break;
    }
    case "needs-you": {
      const cr = atTask("running");
      close(cr);
      measured();
      // Two turns of one thread: the first answered, the second asking a person what the plan froze.
      ev("work_item_needs_human", { node_id: "verification", kind: "stuck", reason: "code review found a race the plan's frozen reindex API cannot fix" }, 0.05);
      close(turn(1, "Code review flags a race between reindex and search: find a fix inside the plan.", true));
      const q = "The race needs a reindex API change the plan froze. Allow the change, or accept the finding and move on?";
      const e = turn(2, "Can it be fixed without touching the reindex API?", false, "needs_context");
      m += 4;
      ev("worker_session_exited", { session_id: e.id, status: "needs_context", wall_ms: 240_000 }, 0);
      Object.assign(e, { wall_ms: 240_000, model: MODEL, tokens_in: 210, tokens_out: 6_100, cost_usd: 0.52 });
      ev("work_item_needs_human", { node_id: "verification", reason: `needs_context: ${q}`, kind: "question" }, 0.5);
      item.status = "needs_human"; item.display_status = "needs_you"; item.needs_context_question = q;
      item.stop = stop("question", "escalation", { reason: `needs_context: ${q}` });
      break;
    }
    case "capped": {
      // The time cap stops its running session (`session_exited`, capped_out), then the item.
      const s = open("implementation.main.implement", "implementation");
      ["Read the search path", "Key the cache by content hash", "Invalidate on reindex"].forEach((title, k) => ev("plan_progress", { node_id: "implementation", task: k + 1, total: 6, title }, 25));
      m -= 75;
      close(s, "capped_out", 120 * 60_000);
      const reason = "`implementation.main.implement` hit its time cap of 120 minutes";
      ev("work_item_needs_human", { node_id: "implementation", reason, kind: "cap" }, 0.05);
      item.status = "needs_human"; item.display_status = "needs_you";
      item.stop = stop("cap", "implementation.main.implement", { reason, facts: { field: "time_cap_minutes", cap_s: 7_200 }, limit: { path: "implementation.main.implement", key: "time_cap_minutes", value: 120, maximum: null }, raisable: true });
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
      item.stop = stop("rate_limit", s.hook_point, { attempt: s.attempt, resume_at: t(133.7), reason: "claude-code hit its rate limit", facts: { harness: "claude-code", fallback: ["codex"], fallback_allowed: ["codex"] } });
      break;
    }
    case "waiting-ci": {
      const s = awaitPolls("merge_request_feedback", "merge_request_feedback.ci.await_ci", "mr.ci", 3, false, t(133));
      item.summary.step = { index: 1, count: 3 };
      item.status = "waiting"; item.display_status = "waiting"; item.retry_at = t(133);
      item.stop = stop("wait", s.hook_point, { resume_at: t(133), reason: "waiting for CI" });
      break;
    }
    case "mr-closed": {
      awaitPolls("merge_request_feedback", "merge_request_feedback.ci.await_ci", "mr.ci", 2, false);
      ev("mr_closed", { ref: MR.number, url: MR.url, by: "mara" }, 2);
      ev("work_item_needs_human", { node_id: "merge_request_feedback", kind: "mr_closed", reason: "MR !142 was closed on the forge without a merge." }, 0.2);
      item.status = "needs_human"; item.display_status = "needs_you";
      item.stop = stop("mr_closed", null, { reason: "MR !142 was closed on the forge without a merge.", facts: { ref: MR.number, url: MR.url } });
      break;
    }
    case "needs-gate": {
      // Round 1, a review that sent it back: asked at t(91), rejected at t(95), the instant the review was submitted.
      m = Math.max(m, 90.9);
      ev("gate_requested", { node_id: "final_review", gate: "final_review" }, 0.1);
      ran("final_review.auto_review", "final_review");
      ev("gate_auto_review_skipped", { gate: "final_review", node_id: "final_review", reason: "passed", note: REVIEWED }, 0.02);
      m = Math.max(m, 94.98);
      ev("gate_rejected", { gate: "final_review", node: "implementation", by: "human", note: "must fix: `commits` can be empty\n\nquestion: why no size bound?", verdict: "reject" }, 0.02);
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
      ran("draft_merge_request.rebase.rebase", "draft_merge_request");
      for (let a = 1; a <= 3; a++) ran("draft_merge_request.open.open", "draft_merge_request", "failed", { attempt: a });
      ev("work_item_needs_human", { node_id: "draft_merge_request", kind: "failed", reason: "The forge refused to open the draft MR." }, 6);
      item.status = "needs_human"; item.display_status = "failed";
      item.summary.step = { index: 2, count: 2 };
      item.stop = stop("failed", "draft_merge_request.open.open", { attempt: 3, reason: "The forge refused to open the draft MR. No handler applies and retrying will not change the answer, so the item stopped instead of looping. Fix the token, then retry from the failed task.", facts: { error: "403 Forbidden · token lacks merge_request:write", tried: "3 times over 6m" } });
      break;
    }
    case "conflict": {
      ran("draft_merge_request.rebase.rebase", "draft_merge_request", "failed", { attempt: 1 });
      ran("draft_merge_request.rebase.rebase", "draft_merge_request", "failed", { attempt: 2 });
      ev("work_item_needs_human", { node_id: "draft_merge_request", kind: "conflict", reason: "Rebasing onto main hit conflicts in 3 files." }, 2);
      item.status = "needs_human"; item.display_status = "needs_you";
      item.summary.step = { index: 1, count: 2 };
      item.stop = stop("conflict", "draft_merge_request.rebase.rebase", { attempt: 2, reason: "Rebasing onto main hit conflicts in 3 files. The on-conflict agent resolved one and stopped on two it could not decide.", facts: { resolved: ["search/config.py"], unresolved: ["search/cache.py", "search/reindex.py"] } });
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
    return { node, tokens_in: sum("tokens_in"), tokens_out: sum("tokens_out"), cost_usd: Math.round(sum("cost_usd") * 10_000) / 10_000, cost_complete: true, wall_ms: sum("wall_ms"), sessions: ss.length, rounds: 1, capped_out: 0 };
  });
  const total = byNode.reduce((a, r) => ({ tokens_in: a.tokens_in + r.tokens_in, tokens_out: a.tokens_out + r.tokens_out, cost_usd: a.cost_usd + r.cost_usd, wall_ms: a.wall_ms + r.wall_ms }), { tokens_in: 0, tokens_out: 0, cost_usd: 0, wall_ms: 0 });
  total.cost_usd = Math.round(total.cost_usd * 10_000) / 10_000;
  item.usage = { total: { ...total, cost_complete: true, sessions: sessions.length, rounds: 1, capped_out: 0 }, by_node: byNode };
  item.budget_cap.spent_usd = total.cost_usd;
  const threads = sc === "needs-gate" ? [
    { id: "th-r1-must", work_item_id: id, gate: "final_review", node_id: "implementation", file_path: "search/cache.py", side: "new", start_line: 12, end_line: 14, quote: " def newest(commits):\n+    latest = max(c.ts for c in commits)\n+    return self._cache[latest]", label: "must_fix", state: "resolved", resolved_at: t(94), created_at: t(92), comments: [{ id: "c-r1-must", author: "you", body: "`commits` can be empty: `max()` of an empty generator raises", created_at: t(92) }] },
    { id: "th-r1-q", work_item_id: id, gate: "final_review", node_id: "implementation", file_path: "search/reindex.py", side: "new", start_line: 30, end_line: 30, quote: "+        self._cache[key] = value", label: "question", state: "open", created_at: t(93), comments: [{ id: "c-r1-q", author: "you", body: "why no size bound?", created_at: t(93) }] },
    { id: "th1", work_item_id: id, gate: null, node_id: "local_review", file_path: "search/cache.py", side: "new", start_line: 40, end_line: 44, label: "question", state: "open", created_at: t(90), comments: [{ id: "c1", author: "you", body: "cache has no size bound", created_at: t(90) }] },
  ] : [];
  const reviews = sc === "needs-gate" ? [{ id: "rv1", gate: "final_review", outcome: "request_changes", summary: "Two things before this ships.", head_sha: "4d1e2f3", submitted_at: t(95) }] : [];
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
    usage: { total: { tokens_in: 0, tokens_out: 0, cost_usd: 0, cost_complete: true, wall_ms: 0, sessions: 0, rounds: 0, capped_out: 0 }, by_node: [] },
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
