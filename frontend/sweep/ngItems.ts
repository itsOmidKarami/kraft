import { hex, logLines, LONG_DESCRIPTION, t, type ItemBundle, type Variant } from "./fixtures";

/**
 * Items for the /ng item page (ux2-W5): the prototype's 15-node V1 chain in
 * every scenario `Kraft Prototype V2.dc.html` draws, plus paused. They live in
 * the scenario's bundles only (the detail route answers them), never in the
 * board list, so no shipped cell changes. Times are T0-based; the ng-item
 * cases fix the page clock at NG_NOW so elapsed and "ago" read the same every run.
 */
export const NG_SCENARIOS = [
  "running", "needs-you", "needs-gate", "escalated", "capped", "done", "cancelled", "archived",
  "failed", "waiting", "conflict", "mr-closed", "worker-lost", "paused",
] as const;
export type NgScenario = (typeof NG_SCENARIOS)[number];
/** T0 + 130 minutes, as a literal: this module loads before fixtures.ts finishes (they import each other). */
export const NG_NOW = "2026-09-13T10:10:00.000Z";

type Node = { id: string; kind: "exec" | "gate"; tasks: string[]; steps: string[][]; gate_after: null; fix_loop?: string; reject_to?: string | null; rebase_bounce_to?: string | null; on_failure?: string[] | null; auto_escalate?: boolean };
const exec = (id: string, steps: Record<string, string[]>, more: Partial<Node> = {}): Node => {
  const s = Object.entries(steps).map(([step, tasks]) => tasks.map((task) => `${id}.${step}.${task}`));
  return { id, kind: "exec", tasks: s.flat(), steps: s, gate_after: null, ...more };
};
const gate = (id: string, more: Partial<Node> = {}): Node => ({ id, kind: "gate", tasks: [], steps: [], gate_after: null, ...more });

export const NG_NODES: Node[] = [
  exec("spec", { write: ["spec"] }),
  gate("spec_approval", { reject_to: "spec" }),
  exec("plan", { write: ["plan"] }),
  gate("plan_approval", { reject_to: "plan" }),
  exec("chain_revision", { revise: ["chain_revision"] }),
  gate("chain_revision_approval", { auto_escalate: true }),
  exec("implementation", { build: ["implement"] }),
  exec("verification", { test: ["unit_tests"], checks: ["lint", "typecheck", "security_scan"], review: ["code_review", "automated_review"] }, { fix_loop: "verification_fix_loop", on_failure: ["verification.repair.repair_pass"] }),
  exec("work_brief", { write: ["work_brief"] }),
  gate("local_review", { reject_to: "implementation" }),
  exec("merge_request", { rebase: ["rebase"], open: ["open_draft"] }, { rebase_bounce_to: "verification" }),
  exec("mr_checks", { ci: ["mr_checks"] }),
  exec("summary", { write: ["summary"] }),
  gate("final_review", { reject_to: "implementation" }),
  exec("release", { ship: ["release"] }),
];

/** Where each scenario stands: the current node (index), and the step it is on there. */
const AT: Record<NgScenario, number> = {
  running: 7, "needs-you": 7, "needs-gate": 13, escalated: 7, capped: 7, done: 15, cancelled: 7, archived: 15,
  failed: 10, waiting: 7, conflict: 10, "mr-closed": 11, "worker-lost": 7, paused: 7,
};

export function buildNgItem(sc: NgScenario, seed: number, variant: Variant): ItemBundle {
  const long = variant === "long";
  const id = hex(seed);
  const cur = AT[sc];
  const currentNode = NG_NODES[cur]?.id ?? null;
  const sessions: any[] = [];
  const events: any[] = [];
  const logs: Record<string, any[]> = {};
  let seq = 0;
  let m = 0;
  const ev = (type: string, payload: Record<string, unknown> = {}, dm = 1) => {
    m += dm;
    const node = payload.node_id ?? payload.node;
    events.push({ seq: ++seq, work_item_id: id, type, payload, node_id: typeof node === "string" ? node : null, created_at: t(m) });
  };
  const sess = (path: string, status: string, over: Record<string, unknown> = {}) => {
    const sid = hex(seed * 100 + sessions.length + 1);
    const node = path.split(".")[0];
    const s = {
      id: sid, work_item_id: id, node_id: node, hook_point: path, status, attempt: 1, round: 0, thread: 1,
      created_at: t(m), started_at: t(m + 0.1), exited_at: ["running", "pending", "rate_limited", "waiting"].includes(status) ? null : t(m + 4),
      tokens_in: 41_200 + sessions.length * 3_100, tokens_out: 5_400 + sessions.length * 410, cost_usd: 0.06 + (sessions.length % 5) * 0.03,
      wall_ms: ["running", "pending"].includes(status) ? null : 4 * 60_000 + (sessions.length % 7) * 21_000,
      model: path.includes("unit_tests") || path.includes("lint") || path.includes("typecheck") || path.includes("mr_checks") ? null : "claude-sonnet-4-5",
      head_sha: hex(seed + 400 + sessions.length).slice(0, 40), session_summary_ref: status === "done" ? `.engineering/sessions/${sid}.md` : null,
      ...over,
    };
    sessions.push(s);
    logs[sid] = logLines(sid, long ? 300 : 12, long);
    return s;
  };
  const runNode = (n: Node, status = "done", attempt = 1) => {
    ev("node_started", { node_id: n.id }, 2);
    for (const path of n.tasks) {
      const s = sess(path, status, { attempt });
      ev("worker_session_created", { session_id: s.id, hook_point: path, node_id: n.id, attempt }, 0.2);
      ev("worker_session_exited", { session_id: s.id, node_id: n.id, status, wall_ms: s.wall_ms }, 4);
    }
    if (n.kind === "gate") {
      ev("gate_requested", { node_id: n.id, gate: n.id }, 0.2);
      ev("gate_approved", { node_id: n.id, gate: n.id, by: n.auto_escalate ? "agent" : "human" }, 3);
    }
    ev("node_completed", { node_id: n.id }, 0.2);
  };

  ev("work_item_created", { title: "Design the caching layer for document search" }, 0);
  ev("chain_loaded", {}, 0.1);
  for (let i = 0; i < Math.min(cur, NG_NODES.length); i++) runNode(NG_NODES[i]);

  const title = long
    ? "Design the caching layer for document search so large repositories stop re-embedding every candidate document on every single query, keyed by content hash"
    : "Design the caching layer for document search";
  const item: any = {
    id, title, repo: "/Users/dev/code/kraft-plugins",
    description: long ? LONG_DESCRIPTION : "Search re-embeds every candidate document on every query, so large repos take 2–4 s per search. Cache embeddings keyed by document content hash, invalidate them when reindex sees a new hash, and keep the cache bounded. The public search API stays unchanged.",
    status: "active", chain_template: "default",
    chain_definition: { template_id: "default", nodes: NG_NODES },
    effective_chain: { template_id: "default", nodes: NG_NODES },
    current_node_id: currentNode, bead_id: "kraft-cb59",
    created_at: t(0), updated_at: t(m),
    auto_gate: true, pending_gate: null, gate_artifact: null, steerable: true,
    node_overrides: long ? { verification: { attempts: 4 } } : {}, node_overrides_count: long ? 1 : 0,
    agent_overrides: null,
    budget_cap: { cap_usd: 5, source: long ? "item" : "policy", spent_usd: 2.41, daily: { spent_usd: 18.2, cap_usd: 50 } },
    worktree_path: `/Users/dev/.kraft/run/worktrees/${id}`, head_sha: hex(seed + 77).slice(0, 40),
    repos: [], attachments: [], mr_ref: null, progress: null, rate_limit: null,
    escalation_threads: [], concerns: [], deferred_findings: [],
    display_status: "running", stop: null,
    summary: { nodes_done: Math.min(cur, NG_NODES.length), nodes_total: NG_NODES.length, gates_passed: NG_NODES.slice(0, cur).filter((n) => n.kind === "gate").length, step: null },
  };
  const stop = (kind: string, task: string | null, more: Record<string, unknown> = {}) => ({ kind, node: currentNode, task, attempt: 1, resume_at: null, reason: null, facts: {}, ...more });

  // The verification node at its third step, attempt 2 of the fix loop (the prototype's running chain).
  const verificationAt = (review: string) => {
    const n = NG_NODES[7];
    ev("node_started", { node_id: n.id }, 2);
    for (const path of n.tasks.slice(0, 4)) {
      const s = sess(path, "done");
      ev("worker_session_exited", { session_id: s.id, node_id: n.id, status: "done", wall_ms: s.wall_ms }, 1);
    }
    ev("fix_cycle_started", { node_id: n.id, cycle: 1, failed_tasks: ["verification.review.code_review"] }, 1);
    for (const path of n.tasks.slice(0, 4)) sess(path, "done", { attempt: 2, round: 1 });
    sess("verification.review.code_review", "done", { round: 0 });
    const cr = sess("verification.review.code_review", review, { attempt: 2, round: 1 });
    ev("worker_session_started", { session_id: cr.id, node_id: n.id }, 0.5);
    item.summary.step = { index: 3, count: 3 };
    return cr;
  };

  switch (sc) {
    case "running": case "escalated": case "paused": case "cancelled": case "worker-lost": {
      verificationAt(sc === "paused" ? "paused" : "running");
      if (sc === "escalated") {
        const e = sess("implementation.escalation.escalation", "done", { node_id: "implementation", hook_point: "escalation" });
        ev("escalation_message", { session_id: e.id, node_id: "implementation", thread: 1, turn: 1, message: "The attached spec says LRU, 50k entries. Answered and resumed." }, 1);
        item.escalation_threads = [{ thread: 1, session_id: e.id, turns: 1, started_at: t(m - 2), ended_at: t(m), status: "done" }];
        item.display_status = "escalated";
      }
      if (sc === "paused") {
        ev("pause_requested", { sessions: [] }, 1);
        item.status = "paused"; item.display_status = "paused";
        item.pending_steer_context = long ? "Look at the invalidation path first: the race only shows when reindex and search overlap." : null;
      }
      if (sc === "cancelled") {
        ev("work_item_cancelled", { reason: long ? "Superseded by kraft-cb61, which moves the cache into the index service; this item's branch stays for reference." : "", node_id: currentNode }, 1);
        item.status = "abandoned"; item.display_status = "cancelled";
      }
      if (sc === "worker-lost") {
        item.status = "waiting"; item.display_status = "waiting";
        // B5 (R2): the shape the worker capability is expected to send; the product hides the card without it.
        item.stop = stop("worker_lost", "verification.review.code_review", { attempt: 2, facts: { worker: "ci-runner-3", last_seen_at: t(128), reassign_at: t(133), workers_online: ["ci-runner-1", "ci-runner-2"] } });
      }
      break;
    }
    case "needs-you": {
      verificationAt("done");
      const e = sess("verification.escalation.escalation", "needs_context", { hook_point: "escalation" });
      const q = "The race needs a reindex API change the plan froze. Allow the change, or accept the finding and move on?";
      ev("escalation_message", { session_id: e.id, node_id: "verification", thread: 1, turn: 2, message: q }, 1);
      ev("work_item_needs_human", { node_id: "verification", reason: `needs_context: ${q}`, kind: "question" }, 0.5);
      item.status = "needs_human"; item.display_status = "needs_you"; item.needs_context_question = q;
      item.escalation_threads = [{ thread: 1, session_id: e.id, turns: 2, started_at: t(m - 40), ended_at: null, status: "needs_context" }];
      item.stop = stop("question", "verification.escalation.escalation", { reason: `needs_context: ${q}` });
      break;
    }
    case "capped": {
      verificationAt("capped_out");
      ev("work_item_needs_human", { node_id: "verification", reason: "Running time hit its 8h cap", kind: "cap" }, 1);
      item.status = "needs_human"; item.display_status = "needs_you";
      item.stop = stop("cap", "verification.review.code_review", { attempt: 2, reason: "Running time hit its 8h cap", facts: { field: "running_time", cap_s: 28_800 } });
      break;
    }
    case "waiting": {
      const cr = verificationAt("rate_limited");
      ev("work_item_rate_limited", { node_id: "verification", retry_at: t(133.7) }, 0.3);
      item.status = "rate_limited"; item.display_status = "waiting"; item.retry_at = t(133.7);
      item.rate_limit = { count: 2, cap: 5 };
      item.stop = stop("rate_limit", cr.hook_point, { attempt: 2, resume_at: t(133.7), reason: "claude-code hit its rate limit", facts: { harness: "claude-code", fallback: ["codex"], fallback_allowed: ["codex"] } });
      break;
    }
    case "needs-gate": {
      ev("node_started", { node_id: "final_review" }, 1);
      ev("gate_requested", { node_id: "final_review", gate: "final_review" }, 0.2);
      item.status = "needs_human"; item.display_status = "needs_you"; item.pending_gate = "final_review";
      // A re-review (ux2-W8): two attempts at the gate, and a review submitted on the first.
      item.attempts = [{ n: 1, sha: "4d1e2f3", base_sha: "1a2b3c4", at: t(3) }, { n: 2, sha: "9c8d7e6", base_sha: "1a2b3c4", at: t(1.1) }];
      item.last_review_sha = "4d1e2f3";
      item.head_sha = "9c8d7e6";
      item.fix_target = { gate: "final_review", node: "implementation", then: ["verification", "work_brief", "local_review", "merge_request", "mr_checks", "summary"], round: { n: 2, max: 3 } };
      item.gate_artifact = ".engineering/reviews/kraft-cb59-review.md";
      item.mr_ref = { number: 142, url: "https://github.com/acme/kraft-plugins/pull/142" };
      item.stop = stop("gate", null);
      break;
    }
    case "failed": {
      const n = NG_NODES[10];
      ev("node_started", { node_id: n.id }, 1);
      sess("merge_request.rebase.rebase", "done");
      for (let a = 1; a <= 3; a++) sess("merge_request.open.open_draft", "failed", { attempt: a });
      ev("work_item_needs_human", { node_id: n.id, kind: "failed", reason: "The forge refused to open the draft MR." }, 6);
      item.status = "needs_human"; item.display_status = "failed";
      item.stop = stop("failed", "merge_request.open.open_draft", { attempt: 3, reason: "The forge refused to open the draft MR. No handler applies and retrying will not change the answer, so the item stopped instead of looping. Fix the token, then retry from the failed task.", facts: { error: "403 Forbidden · token lacks merge_request:write", tried: "3 times over 6m" } });
      break;
    }
    case "conflict": {
      ev("node_started", { node_id: "merge_request" }, 1);
      sess("merge_request.rebase.rebase", "failed", { attempt: 2 });
      ev("work_item_needs_human", { node_id: "merge_request", kind: "conflict", reason: "Rebasing onto main hit conflicts in 3 files." }, 2);
      item.status = "needs_human"; item.display_status = "needs_you";
      item.stop = stop("conflict", "merge_request.rebase.rebase", { attempt: 2, reason: "Rebasing onto main hit conflicts in 3 files. The on-conflict agent resolved one and stopped on two it could not decide.", facts: { resolved: ["search/config.py"], unresolved: ["search/cache.py", "search/reindex.py"] } });
      break;
    }
    case "mr-closed": {
      ev("mr_opened", { node_id: "merge_request", number: 142, url: "https://github.com/acme/kraft-plugins/pull/142" }, 0.5);
      ev("node_started", { node_id: "mr_checks" }, 1);
      sess("mr_checks.ci.mr_checks", "waiting");
      ev("mr_closed", { ref: 142, url: "https://github.com/acme/kraft-plugins/pull/142", by: "mara" }, 2);
      ev("work_item_needs_human", { node_id: "mr_checks", kind: "mr_closed", reason: "MR !142 was closed on the forge without a merge." }, 0.2);
      item.mr_ref = { number: 142, url: "https://github.com/acme/kraft-plugins/pull/142" };
      item.status = "needs_human"; item.display_status = "needs_you";
      item.stop = stop("mr_closed", null, { reason: "MR !142 was closed on the forge without a merge.", facts: { ref: 142, url: "https://github.com/acme/kraft-plugins/pull/142" } });
      break;
    }
    case "done": case "archived": {
      ev("work_item_completed", {}, 1);
      item.status = "completed"; item.display_status = sc === "archived" ? "archived" : "done";
      item.mr_ref = { number: 142, url: "https://github.com/acme/kraft-plugins/pull/142" };
      if (sc === "archived") { item.archived_at = t(m + 30); item.archived_by = "you"; }
      break;
    }
  }
  item.updated_at = t(m);
  const byNode = [...new Set(sessions.map((s) => s.node_id))].map((node) => {
    const ss = sessions.filter((s) => s.node_id === node);
    const sum = (k: string) => ss.reduce((a, s) => a + (s[k] ?? 0), 0);
    return { node, tokens_in: sum("tokens_in"), tokens_out: sum("tokens_out"), cost_usd: sum("cost_usd"), cost_complete: true, wall_ms: sum("wall_ms"), sessions: ss.length, rounds: 1, capped_out: 0 };
  });
  const total = byNode.reduce((a, r) => ({ tokens_in: a.tokens_in + r.tokens_in, tokens_out: a.tokens_out + r.tokens_out, cost_usd: a.cost_usd + r.cost_usd, wall_ms: a.wall_ms + r.wall_ms }), { tokens_in: 0, tokens_out: 0, cost_usd: 0, wall_ms: 0 });
  item.usage = { total: { ...total, cost_complete: true, sessions: sessions.length, rounds: 1, capped_out: 0 }, by_node: byNode };
  const threads = sc === "needs-gate" ? [{ id: "th1", work_item_id: id, gate: null, node_id: "local_review", file_path: "search/cache.py", side: "new", start_line: 40, end_line: 44, label: "question", state: "open", created_at: t(90), comments: [{ id: "c1", author: "you", body: "cache has no size bound", created_at: t(90) }] }] : [];
  return { item, sessions, events, logs, threads };
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
