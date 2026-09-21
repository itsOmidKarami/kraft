/**
 * Deterministic API fixtures for the UI sweep. Shapes follow
 * frontend/src/types/*.ts. Every display state deriveState() can produce is
 * reachable here in milliseconds — no orchestrator, no fake agent.
 *
 * Variants:
 *   default — realistic data, one item per state, medium titles
 *   long    — long titles/descriptions/paths, 12 repos, 40-node timelines,
 *             600-line logs, 40-file diffs, unbreakable hash tokens
 *   many    — 60 board rows
 *   empty   — fresh install: no items, no repos
 */

export type Variant = "default" | "long" | "many" | "empty";

export type DisplayState =
  | "running" | "rate_limited" | "waiting" | "not_started" | "paused" | "gate"
  | "capped" | "budget" | "question" | "escalating" | "escalated" | "done"
  | "abandoned" | "archived";

export const STATES: DisplayState[] = [
  "running", "rate_limited", "waiting", "not_started", "paused", "gate", "capped",
  "budget", "question", "escalating", "escalated", "done", "abandoned", "archived",
];

/* ── ids & time ──────────────────────────────────────────────────────────── */

/** 32-hex ids like the real ones (c7446dca30d840a8a69977c6649a7b11). */
export function hex(seed: number): string {
  let s = "";
  let x = (seed * 2654435761) >>> 0;
  while (s.length < 32) {
    x = (x ^ (x << 13)) >>> 0; x = (x ^ (x >>> 17)) >>> 0; x = (x ^ (x << 5)) >>> 0;
    s += x.toString(16).padStart(8, "0");
  }
  return s.slice(0, 32);
}

const T0 = Date.parse("2026-09-13T08:00:00Z");
const NOW = Date.now();
const t = (minutes: number) => new Date(T0 + minutes * 60_000).toISOString();
/** Relative to now, for "next check in 4m" style fields. */
const fromNow = (minutes: number) => new Date(NOW + minutes * 60_000).toISOString();

/* ── repos ───────────────────────────────────────────────────────────────── */

const REPO_PATHS = [
  "/Users/dev/code/kraft",
  "/Users/dev/code/kraft-plugins",
  "/Users/dev/code/acme-billing-platform",
];
const LONG_REPO_PATHS = [
  ...REPO_PATHS,
  "/Volumes/Work/clients/acme-corporation/monorepo/services/payment-reconciliation-service",
  "/Users/dev/code/a-repository-with-an-unreasonably-long-name-that-should-ellipsize-somewhere",
  "/home/ci/workspace/infra-terraform-modules",
  "/home/ci/workspace/infra-kubernetes-manifests",
  "/Users/dev/code/mobile-ios",
  "/Users/dev/code/mobile-android",
  "/Users/dev/code/design-system-web",
  "/Users/dev/code/docs-site",
  "/Users/dev/code/data-pipelines-airflow",
];

export function repo(path: string, i: number, long = false) {
  const name = path.split("/").pop()!;
  return {
    path, name,
    default_chain_template: i % 3 === 2 ? "quick-task" : "default",
    test_command: i % 4 === 3 ? null : long ? "uv run pytest -q tests/ --maxfail=1 --disable-warnings -p no:cacheprovider" : "uv run pytest -q",
    test_scopes: long && i % 2 === 0 ? [{ paths: ["frontend/**"], command: "cd frontend && npm test -- --run" }] : null,
    forge: i % 2 === 0 ? "gitlab" : "github",
    project: long ? `acme-corporation/platform-engineering/${name}` : `acme/${name}`,
    enabled: i % 5 !== 4,
    default_model: i === 1 ? "claude-opus-4-1" : null,
    deny_tools: i === 0 ? ["WebFetch", "Bash(rm -rf*)"] : [],
    steering: i === 0 ? ["house-style", "commit-messages"] : [],
    allow_cross_repo: i === 2,
    default_root_merge_policy: (["bump", "skip", "bump_no_mr"] as const)[i % 3],
    submodules: long && i === 0
      ? [
          { path: "vendor/kraft-lite", enabled: true, test_command: "pytest -q", chain_override: null },
          { path: "vendor/some-deeply/nested/submodule/path/that-is-long", enabled: false, test_command: null, chain_override: "quick-task" },
        ]
      : [],
  };
}

/* ── chains ──────────────────────────────────────────────────────────────── */

const HOOK: Record<string, string> = {
  spec: "on.spec.requested", plan: "on.plan.requested", implement: "on.implementation.start",
  verify: "on.test.run", review: "on.review.requested", pre_mr_rebase: "on.mr.rebase",
  open_mr: "on.mr.open", ci_poll: "on.ci.poll",
};

export const DEFAULT_NODES = [
  { id: "spec", tasks: ["on.spec.requested"], gate_after: "spec_approval", reject_to: null },
  { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval", reject_to: "spec" },
  { id: "implement", tasks: ["on.implementation.start"], gate_after: null, fix_loop: "verify_fix_loop" },
  { id: "verify", tasks: ["on.test.run", "on.lint.run"], gate_after: null, on_failure: ["on.test.fix"] },
  { id: "review", tasks: ["on.review.requested"], gate_after: "code_review", auto_escalate: true, reject_to: "implement" },
  { id: "pre_mr_rebase", tasks: ["on.mr.rebase"], gate_after: null },
  { id: "open_mr", tasks: ["on.mr.open"], gate_after: "human_review_approval" },
  { id: "ci_poll", tasks: ["on.ci.poll"], gate_after: null },
];
export const QUICK_NODES = [
  { id: "implement", tasks: ["on.implementation.start"], gate_after: null, fix_loop: "verify_fix_loop" },
  { id: "verify", tasks: ["on.test.run"], gate_after: null },
  { id: "open_mr", tasks: ["on.mr.open"], gate_after: null },
];

/* ── titles ──────────────────────────────────────────────────────────────── */

const TITLES: Record<DisplayState, string> = {
  running: "Reuse what we measured in the fix loop",
  rate_limited: "Add retry budget to the CI poller",
  waiting: "Wait for the rebase pipeline before opening the MR",
  not_started: "Migrate the settings store to SQLite",
  paused: "Investigate the flaky import test",
  gate: "Design the caching layer for document search",
  capped: "Rewrite the YAML parser without pyyaml",
  budget: "Backfill analytics for archived items",
  question: "Rename beads → tickets across the CLI",
  escalating: "Fix the peek overlay squeezing board rows",
  escalated: "Deduplicate findings across fix cycles",
  done: "Fix add() so the tests pass",
  abandoned: "Spike: replace zustand with plain context",
  archived: "Ship the segmented progress bar",
};
const LONG_TITLES: Record<DisplayState, string> = {
  running: "Reuse what we measured: carry findings_measured from the verify node into the fix loop's system prompt instead of re-running every plugin on each cycle, and surface the delta on the gate card",
  rate_limited: "Add a retry budget to the CI poller so a pipeline that flaps between pending and running for 40 minutes does not page a human every time the rate limit resets",
  waiting: "Wait for the pre_mr_rebase pipeline to settle before open_mr runs — currently the MR opens against a SHA that is already superseded by the rebase commit",
  not_started: "Migrate the settings store from a hand-rolled JSON file to SQLite (WAL mode) so concurrent writers from the CLI and the daemon stop clobbering each other",
  paused: "Investigate the flaky import test in tests/test_index_ingest.py::test_ingest_dedupes_identical_chunks_across_repos — fails ~1 in 12 runs on CI only",
  gate: "Design the caching layer for document search: embedding cache keyed by (repo, path, blob_sha), invalidation on git_scan, and a size cap with LRU eviction",
  capped: "Rewrite the YAML parser without pyyaml: preserve key order, comments, and the anchors default.yaml relies on, while keeping templates.py's error messages byte-identical",
  budget: "Backfill analytics for archived items created before the usage rollup existed (c7446dca30d840a8a69977c6649a7b11 and ~340 siblings) without double counting",
  question: "Rename beads → tickets across the CLI, the MCP surface, and every docstring — but keep the `bd` shell-out and the on-disk .beads/ directory untouched",
  escalating: "Fix the peek overlay squeezing board rows at 1100px: the peek must overlay, rows must not move, and the scrim must not swallow the sidebar toggle",
  escalated: "Deduplicate findings across fix cycles so a finding the judge already downgraded in cycle 1 does not re-enter the loop as critical in cycle 3",
  done: "Fix add() so the tests pass",
  abandoned: "Spike: replace zustand with plain React context and measure the re-render cost on a 400-row board",
  archived: "Ship the segmented progress bar on the board row, the peek, the item hero, the stage-graph pill, search results, and the phone stage list",
};

const LONG_DESCRIPTION = `## Context

The verify node already produces a \`findings_measured\` event with every finding at every severity. The fix loop then re-runs **every** plugin on each cycle, which costs ~$0.40 and 3 minutes per cycle on \`acme-billing-platform\`.

## What to change

1. Read the last \`findings_measured\` for the node before dispatching the fix agent.
2. Put the *unresolved* findings (by \`file:line:message\` identity) at the top of the system prompt.
3. Re-run only the plugins that reported a finding — \`on.lint.run\` and \`on.test.run\` stay unconditional.

## Out of scope

- Changing the judge's stop_downgrade rules (see Kraft-a4js).
- Anything in \`frontend/\` beyond the gate card's deferred-findings line.

Related: \`.engineering/specs/2026-09-12-reuse-what-we-measured.md\`, session \`b06bc30ddb55460eb61908b2e0447978\`.`;

/* ── builders ────────────────────────────────────────────────────────────── */

type Ev = { seq: number; work_item_id: string; type: string; payload: Record<string, unknown>; created_at: string };

export interface ItemBundle {
  item: any;
  sessions: any[];
  events: Ev[];
  logs: Record<string, any[]>;
}

function logLines(sessionId: string, n: number, long: boolean): any[] {
  const out: any[] = [];
  const srcs = ["sys", "stdout", "agent", "tool"] as const;
  for (let i = 1; i <= n; i++) {
    const src = srcs[i % 4];
    let text =
      src === "sys" ? `[kraft] dispatch ${sessionId.slice(0, 8)} attempt 1 cwd=/Users/dev/.kraft/worktrees/${sessionId.slice(0, 12)}`
      : src === "stdout" ? `tests/test_index_ingest.py::test_ingest_dedupes_identical_chunks_across_repos PASSED [ ${String(i).padStart(3)}%]`
      : src === "agent" ? `Reading frontend/src/views/work_item/Inspector/Changes.tsx to see how the tree groups files before I touch the diff pane.`
      : `{"type":"tool_use","name":"Edit","input":{"file_path":"frontend/src/views/work_item/Inspector/Changes.tsx","old_string":"const rows = files.map(","new_string":"const rows = groupTree(files).map("}}`;
    if (long && i % 37 === 0) text = "x".repeat(600) + " " + "https://gitlab.example.com/acme-corporation/platform-engineering/payment-reconciliation-service/-/merge_requests/1842/diffs?commit_id=" + hex(i) + " " + "y".repeat(1200);
    out.push({
      n: i, t: t(i / 10), src, text,
      ...(src === "tool" ? { summary: "Edit frontend/src/views/work_item/Inspector/Changes.tsx" } : {}),
    });
  }
  return out;
}

export function buildItem(state: DisplayState, seed: number, variant: Variant): ItemBundle {
  const long = variant === "long";
  const id = hex(seed);
  const quick = ["done", "paused", "abandoned", "capped"].includes(state);
  const nodes = quick ? QUICK_NODES : DEFAULT_NODES;
  const repoPath = (long ? LONG_REPO_PATHS : REPO_PATHS)[seed % (long ? LONG_REPO_PATHS.length : REPO_PATHS.length)];
  const title = (long ? LONG_TITLES : TITLES)[state];

  // Where in the chain the item sits.
  const idx: Record<DisplayState, number> = {
    running: quick ? 0 : 2, rate_limited: 2, waiting: 7, not_started: -1, paused: 0, gate: 4,
    capped: 1, budget: 2, question: 2, escalating: 4, escalated: 4, done: nodes.length,
    abandoned: 1, archived: nodes.length,
  };
  const cur = idx[state];
  const currentNode = cur >= 0 && cur < nodes.length ? nodes[cur].id : null;

  const events: Ev[] = [];
  const sessions: any[] = [];
  const logs: Record<string, any[]> = {};
  let seq = 0;
  let m = 0;
  const ev = (type: string, payload: Record<string, unknown> = {}, dm = 1) => {
    m += dm;
    events.push({ seq: ++seq, work_item_id: id, type, payload, created_at: t(m) });
  };
  const sess = (node: string, status: string, over: Record<string, unknown> = {}, hook = HOOK[node] ?? "on.task") => {
    const sid = hex(seed * 100 + sessions.length + 1);
    const s = {
      id: sid, work_item_id: id, node_id: node, hook_point: hook, status, attempt: 1, round: 0,
      created_at: t(m), started_at: t(m + 0.2), exited_at: status === "running" || status === "pending" ? null : t(m + 6),
      tokens_in: status === "pending" ? null : 48_210 + sessions.length * 7_311,
      tokens_out: status === "pending" ? null : 6_120 + sessions.length * 911,
      cost_usd: status === "pending" ? null : 0.41 + sessions.length * 0.17,
      wall_ms: status === "running" || status === "pending" ? null : 6 * 60_000 + sessions.length * 13_000,
      model: hook.includes("test") || hook.includes("mr") || hook.includes("ci") ? null : "claude-opus-4-1",
      head_sha: hex(seed + 500 + sessions.length).slice(0, 40),
      session_summary_ref: status === "done" || status === "done_with_concerns" ? `.engineering/sessions/${sid}.md` : null,
      ...over,
    };
    sessions.push(s);
    logs[sid] = logLines(sid, long ? 600 : 40, long);
    return s;
  };

  ev("work_item_created", { title, repo: repoPath });
  ev("chain_loaded", { template_id: quick ? "quick-task" : "default", nodes: nodes.map((n) => n.id) });

  // Completed nodes before the current one.
  const upto = cur < 0 ? 0 : Math.min(cur, nodes.length);
  for (let i = 0; i < upto; i++) {
    const n = nodes[i];
    ev("node_started", { node_id: n.id });
    const s = sess(n.id, n.id === "review" && long ? "done_with_concerns" : "done");
    ev("worker_session_created", { session_id: s.id, hook_point: s.hook_point, node_id: n.id }, 0.1);
    ev("worker_session_started", { session_id: s.id, node_id: n.id }, 0.2);
    if (n.id === "implement") {
      const total = long ? 40 : 6;
      const shown = long ? 40 : 6;
      for (let k = 1; k <= shown; k++) ev("plan_progress", { node_id: n.id, task: k, total, title: long ? `Task ${k}: ${LONG_TITLES.running.slice(0, 70)}` : ["Read the verify contract", "Thread findings into the prompt", "Skip clean plugins", "Update gate card", "Tests", "Docs"][k - 1] }, 1.5);
    }
    if (n.id === "verify") {
      ev("findings_measured", { node_id: n.id, findings: [
        { severity: "important", message: "Unused import `Optional` in kraft/executor/fix_loop.py", file: "kraft/executor/fix_loop.py", line: 4, source_plugin: "ruff" },
        { severity: "minor", message: "Line too long (131 > 120)", file: "kraft/executor/fix_loop.py", line: 88, source_plugin: "ruff" },
        { severity: "critical", message: "test_fix_loop_carries_findings fails: AssertionError", file: "tests/test_fix_loop.py", line: 212, source_plugin: "pytest" },
      ], noop_hooks: ["on.security.scan"] }, 2);
      ev("fix_cycle_started", { node_id: n.id, cycle: 1, failed_tasks: ["on.test.run"] }, 0.5);
      const f = sess("implement", "done", { round: 1 });
      ev("worker_session_created", { session_id: f.id, hook_point: f.hook_point, node_id: "implement" }, 0.1);
      ev("worker_session_exited", { session_id: f.id, node_id: "implement", status: "done", wall_ms: 402_000 }, 6);
      ev("judge_verdict", { node_id: n.id, verdict: "continue", reasoning: "one critical remains; retrying" }, 0.3);
    }
    ev("worker_session_exited", { session_id: s.id, node_id: n.id, status: s.status, wall_ms: 6 * 60_000, ...(s.status === "done_with_concerns" ? { concerns: "the auto_escalate reviewer disagreed with the plan's scope" } : {}) }, 6);
    if (n.gate_after && i < upto) {
      ev("gate_requested", { node_id: n.id, gate: n.gate_after, artifact: `.engineering/${n.id}s/2026-09-12-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, 48)}.md` }, 0.2);
      if (long && n.id === "plan") ev("gate_rejected", { node_id: n.id, gate: n.gate_after, note: "The plan skips the invalidation story. Add a section on what happens when git_scan sees a blob_sha change mid-query." }, 30);
      ev("gate_approved", { node_id: n.id, gate: n.gate_after }, long ? 45 : 12);
    }
    ev("node_completed", { node_id: n.id }, 0.2);
  }

  // The current node.
  let item: any = {
    id, title, repo: repoPath,
    description: long ? LONG_DESCRIPTION : seed % 2 === 0 ? "Carry the verify node's findings into the fix agent's prompt instead of re-running every plugin per cycle." : null,
    status: "active",
    chain_template: quick ? "quick-task" : "default",
    chain_definition: { template_id: quick ? "quick-task" : "default", nodes },
    current_node_id: currentNode,
    bead_id: `kraft-${hex(seed + 9).slice(0, 4)}`,
    created_at: t(0), updated_at: t(m),
    auto_gate: seed % 3 === 0,
    pending_gate: null, gate_artifact: null,
    steerable: true,
    node_overrides: long ? { review: { auto_escalate: false } } : {},
    node_overrides_count: long ? 1 : 0,
    budget_cap: { cap_usd: long ? 25 : 5, source: long ? "item" : "policy", spent_usd: 2.41 },
    root_merge_policy: "bump",
    worktree_path: `/Users/dev/.kraft/worktrees/${id.slice(0, 12)}`,
    head_sha: hex(seed + 77).slice(0, 40),
    repos: long ? [
      { repo: repoPath, path: repoPath, role: "root", merge_rank: 0, state: "clean" },
      ...LONG_REPO_PATHS.slice(3, 3 + 11).map((p, i) => ({ repo: p, path: `${repoPath}/vendor/${p.split("/").pop()}`, role: "submodule", merge_rank: i + 1, state: i % 3 === 0 ? "dirty" : "clean" })),
    ] : [],
    attachments: long ? [{ kind: "spec", path: ".engineering/specs/2026-09-12-reuse-what-we-measured.md" }] : [],
    mr_ref: null, progress: null, rate_limit: null,
  };

  const startCurrent = (status: string) => {
    ev("node_started", { node_id: currentNode });
    const s = sess(currentNode!, status);
    ev("worker_session_created", { session_id: s.id, hook_point: s.hook_point, node_id: currentNode }, 0.1);
    if (status !== "pending") ev("worker_session_started", { session_id: s.id, node_id: currentNode }, 0.2);
    return s;
  };
  const progress = (c: number, total: number) => ({
    current: c, total, title: long ? LONG_TITLES.running.slice(0, 90) : "Thread findings into the prompt",
    tasks: Array.from({ length: total }, (_, i) => ({ n: i + 1, title: long ? `Task ${i + 1}: ${LONG_TITLES.gate.slice(0, 60)}` : `Task ${i + 1}`, state: i + 1 < c ? "done" : i + 1 === c ? "current" : "pending" })),
  });

  switch (state) {
    case "running": {
      startCurrent("running");
      if (!quick) {
        const p = long ? [12, 40] : [3, 6];
        for (let k = 1; k <= p[0]; k++) ev("plan_progress", { node_id: currentNode, task: k, total: p[1], title: progress(k, p[1]).tasks[k - 1].title }, 2);
        item.progress = progress(p[0], p[1]);
      }
      break;
    }
    case "rate_limited": {
      startCurrent("rate_limited");
      ev("work_item_rate_limited", { node_id: currentNode, retry_at: fromNow(4) }, 3);
      item.status = "rate_limited"; item.retry_at = fromNow(4); item.rate_limit = { count: 2, cap: 5 };
      item.progress = progress(2, 6);
      break;
    }
    case "waiting": {
      startCurrent("waiting");
      ev("work_item_waiting", { node_id: currentNode, retry_at: fromNow(9) }, 1);
      item.status = "waiting"; item.retry_at = fromNow(9);
      item.mr_ref = { number: 1842, url: "https://gitlab.example.com/acme/kraft/-/merge_requests/1842" };
      break;
    }
    case "not_started": {
      item.status = "paused"; item.current_node_id = null;
      break;
    }
    case "paused": {
      startCurrent("paused");
      ev("work_item_paused", { node_id: currentNode }, 4);
      item.status = "paused"; item.pending_steer_context = long ? "Look at the CI-only environment first: the failure never reproduces locally." : null;
      break;
    }
    case "gate": case "escalating": case "escalated": {
      const s = startCurrent("done");
      ev("worker_session_exited", { session_id: s.id, node_id: currentNode, status: "done", wall_ms: 300_000 }, 5);
      const artifact = `.engineering/reviews/2026-09-13-${long ? "design-the-caching-layer-for-document-search-embedding-cache-keyed-by-repo-path-blob-sha" : "caching-layer"}.md`;
      ev("gate_requested", { node_id: currentNode, gate: "code_review", artifact }, 0.2);
      item.status = "needs_human"; item.pending_gate = "code_review"; item.gate_artifact = artifact;
      item.deferred_findings = [
        { severity: "minor", message: "Docstring missing on `combine()`", file: "kraft/progress.py", line: 41, source_plugin: "ruff" },
        { severity: "minor", message: "Consider `functools.cache` here", file: "kraft/index/embed.py", line: 12, source_plugin: "reviewer" },
      ];
      item.concerns = long ? ["the reviewer could not run the GitLab CI locally, so the pipeline step is unverified"] : [];
      if (long) item.judge_stop_note = [{ node_id: "verify", reasoning: "two remaining findings are pre-existing on main and not introduced by this change", findings: [
        { severity: "important", message: "mutable default argument", file: "kraft/store.py", line: 99, source_plugin: "ruff" },
        { severity: "important", message: "broad except", file: "kraft/api.py", line: 512, source_plugin: "ruff" },
      ] }];
      if (state !== "gate") {
        const e1 = sess(currentNode!, state === "escalating" ? "running" : "done", { attempt: 1, thread: 1 }, "escalation");
        ev("escalation_message", { session_id: e1.id, node_id: currentNode, auto: state === "escalating", thread: 1, turn: 1, message: long ? "The reviewer flagged the invalidation path as risky. Is the blob_sha check sufficient when a repo is force-pushed, or do we also need to compare the tree hash? Please read the review and decide whether we ship as-is." : "Is the blob_sha check enough after a force-push?" }, 1);
        if (state === "escalated") {
          ev("worker_session_exited", { session_id: e1.id, node_id: currentNode, status: "done", wall_ms: 190_000 }, 3);
          if (long) {
            // Fixture: escalated `long` item gets 2 threads (3 + 1 turns) --
            // e1..e3 continue thread 1, e4 starts a fresh thread 2
            // (ESCALATION_THREADS_SPEC.md §6, Kraft-dkb6g).
            const e2 = sess(currentNode!, "done", { attempt: 2, thread: 1 }, "escalation");
            ev("escalation_message", { session_id: e2.id, node_id: currentNode, auto: false, thread: 1, turn: 2, message: "Follow-up: what about submodules?" }, 1);
            ev("worker_session_exited", { session_id: e2.id, node_id: currentNode, status: "done", wall_ms: 120_000 }, 2);
            const e3 = sess(currentNode!, "done", { attempt: 3, thread: 1 }, "escalation");
            ev("escalation_message", { session_id: e3.id, node_id: currentNode, auto: false, thread: 1, turn: 3, message: "And the root repo's own submodule pointer?" }, 1);
            ev("worker_session_exited", { session_id: e3.id, node_id: currentNode, status: "done", wall_ms: 90_000 }, 2);
            const e4 = sess(currentNode!, "done", { attempt: 4, thread: 2 }, "escalation");
            ev("escalation_message", { session_id: e4.id, node_id: currentNode, auto: false, thread: 2, turn: 1, message: "New thread: let's start over with just the cache-key question." }, 1);
            ev("worker_session_exited", { session_id: e4.id, node_id: currentNode, status: "done", wall_ms: 100_000 }, 2);
          }
        }
      }
      break;
    }
    case "capped": {
      startCurrent("capped_out");
      // W13 · E: each fix cycle is a round with its own fix session; the judge
      // lets the first two continue, the third hits the cap with no verdict.
      for (let c = 1; c <= 3; c++) {
        ev("fix_cycle_started", { node_id: currentNode, cycle: c, failed_tasks: ["on.test.run"] }, 3);
        const fix = sess(currentNode!, "done", { round: c, wall_ms: 70_000 }, "on.test.fix");
        ev("worker_session_created", { session_id: fix.id, hook_point: fix.hook_point, node_id: currentNode }, 0.1);
        ev("worker_session_exited", { session_id: fix.id, node_id: currentNode, status: "done", wall_ms: 70_000 }, 1.2);
        ev("findings_measured", { node_id: currentNode, findings: [{ severity: "critical", message: `test_parse_anchors fails (cycle ${c})`, file: "tests/test_templates.py", line: 77, source_plugin: "pytest" }] }, 1);
        if (c < 3) ev("judge_verdict", { node_id: currentNode, verdict: "continue", reasoning: `one critical remains after cycle ${c}; retrying` }, 0.3);
      }
      ev("work_item_needs_human", { node_id: currentNode, reason: "verify_fix_loop hit its cap", capped: { cycles: 3, attempts: 3 } }, 1);
      item.status = "needs_human"; item.stop_reason = "verify_fix_loop hit its cap";
      break;
    }
    case "budget": {
      startCurrent("failed");
      ev("work_item_needs_human", { node_id: currentNode, reason: "spend cap reached", budget: { scope: "work_item", spent_usd: 5.12, cap_usd: 5 } }, 2);
      item.status = "needs_human"; item.stop_reason = "spend cap reached";
      item.budget_cap = { cap_usd: 5, source: "policy", spent_usd: 5.12 };
      break;
    }
    case "question": {
      startCurrent("needs_context");
      const q = long
        ? "The CLI has 41 call sites that print the word 'bead' to the user and 12 that use it as a JSON key consumed by the MCP server. Renaming the JSON keys breaks any external MCP client. Should I (a) rename both and bump the MCP schema version, (b) rename only user-facing strings, or (c) add aliases and deprecate over two releases?"
        : "Should the JSON keys be renamed too, or only the user-facing strings?";
      ev("work_item_needs_human", { node_id: currentNode, reason: "agent needs context", question: q }, 2);
      item.status = "needs_human"; item.needs_context_question = q; item.stop_reason = "agent needs context";
      break;
    }
    case "done": case "archived": {
      ev("work_item_completed", {}, 1);
      item.status = "completed";
      item.mr_ref = { number: 1837, url: "https://gitlab.example.com/acme/kraft/-/merge_requests/1837" };
      if (state === "archived") { item.archived_at = t(m + 60); item.archived_by = seed % 2 ? "you" : "auto"; }
      break;
    }
    case "abandoned": {
      startCurrent("failed");
      ev("work_item_abandoned", { node_id: currentNode }, 2);
      item.status = "abandoned";
      break;
    }
  }
  item.updated_at = t(m);

  const total = sessions.reduce((a, s) => ({ tokens_in: a.tokens_in + (s.tokens_in ?? 0), tokens_out: a.tokens_out + (s.tokens_out ?? 0), cost_usd: a.cost_usd + (s.cost_usd ?? 0), wall_ms: a.wall_ms + (s.wall_ms ?? 0) }), { tokens_in: 0, tokens_out: 0, cost_usd: 0, wall_ms: 0 });
  const byNode = [...new Set(sessions.map((s) => s.node_id))].map((node) => {
    const ss = sessions.filter((s) => s.node_id === node);
    return { node, tokens_in: ss.reduce((a, s) => a + (s.tokens_in ?? 0), 0), tokens_out: ss.reduce((a, s) => a + (s.tokens_out ?? 0), 0), cost_usd: ss.reduce((a, s) => a + (s.cost_usd ?? 0), 0), cost_complete: true, wall_ms: ss.reduce((a, s) => a + (s.wall_ms ?? 0), 0), sessions: ss.length, rounds: Math.max(...ss.map((s) => s.round)) + 1, capped_out: ss.filter((s) => s.status === "capped_out").length };
  });
  item.usage = { total: { ...total, cost_complete: state !== "waiting", sessions: sessions.length, rounds: 1, capped_out: state === "capped" ? 1 : 0 }, by_node: byNode };
  item.effective_chain = { template_id: item.chain_template, nodes: nodes.map((n) => ({ ...n, ...(item.node_overrides[n.id] ?? {}) })) };

  return { item, sessions, events, logs };
}

/* ── per-item side data ──────────────────────────────────────────────────── */

const FILES_DEFAULT = [
  "kraft/executor/fix_loop.py", "kraft/executor/dispatch.py", "kraft/progress.py",
  "tests/test_fix_loop.py", "frontend/src/views/work_item/ActionBar/GateCard.tsx",
  "docs/superpowers/specs/2026-09-12-reuse-what-we-measured.md",
];
const FILES_LONG = [
  ...FILES_DEFAULT,
  ...Array.from({ length: 34 }, (_, i) => `frontend/src/views/work_item/${["Inspector", "RightPane", "ActionBar"][i % 3]}/${["Changes", "Config", "Documents", "Timeline", "Tasks", "Diff", "Doc", "Events", "Log", "Composer", "BudgetComposer"][i % 11]}${i > 10 ? i : ""}.tsx`),
  "a/very/deeply/nested/directory/structure/that/keeps/going/and/going/until/the/tree/has/to/do/something/about/it/component.tsx",
  `frontend/e2e-shots/${hex(3)}.png`,
];

export function diffFor(id: string, variant: Variant) {
  const files = (variant === "long" ? FILES_LONG : FILES_DEFAULT).map((path, i) => ({ path, insertions: 3 + (i * 7) % 90, deletions: (i * 3) % 40 }));
  const hunk = (p: string) => `diff --git a/${p} b/${p}
index 3f2a1b0..9c8d7e6 100644
--- a/${p}
+++ b/${p}
@@ -1,12 +1,18 @@
 import json
-from typing import Optional
+from typing import Optional, Sequence

-def combine(plan, reports, commits):
-    return max(reports or [0])
+def combine(plan: Sequence[str], reports: Sequence[int], commits: Sequence[str]) -> int:
+    """Where the implementer is: the highest task any signal names."""
+    latest = max(reports or [0])
+    named = max((task_from_subject(c) for c in commits), default=0)
+    return max(latest, named)
${variant === "long" ? "+    # " + "a very long line that never wraps because it is one token: ".repeat(6) + hex(1) + "\n" : ""}
`;
  return {
    work_item_id: id, base_ref: "main",
    files: files.slice(0, Math.ceil(files.length / 2)),
    diff: files.slice(0, Math.ceil(files.length / 2)).map((f) => hunk(f.path)).join("\n"),
    untracked: variant === "long" ? ["scratch/notes.md", "frontend/e2e-shots/"] : [],
    truncated: variant === "long",
    landed: {
      commits: variant === "long" ? Array.from({ length: 14 }, (_, i) => `${hex(40 + i).slice(0, 7)} Task ${i + 1}: ${LONG_TITLES.running.slice(0, 60)}`) : ["a1b2c3d Task 1: read the verify contract", "d4e5f6a Task 2: thread findings into the prompt"],
      files: files.slice(Math.ceil(files.length / 2)),
      diff: files.slice(Math.ceil(files.length / 2)).map((f) => hunk(f.path)).join("\n"),
      truncated: false,
    },
  };
}

export function documentsFor(item: any, variant: Variant) {
  const long = variant === "long";
  const slug = item.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").slice(0, long ? 110 : 40);
  // Two documents attached at intake in every variant, at the real shape (W10.D):
  // a plan in a Claude worktree, 90+ chars, so the row shows both chips and a path that must cut.
  const wt = `.claude/worktrees/${item.id}/docs/superpowers/plans`;
  const docs: { kind: string; node: string; hook: string; path: string; title: string; attached?: string; headingless?: boolean }[] = [
    { kind: "specs", node: "spec", hook: "on.spec.requested", path: `${wt}/2026-09-12-${slug}-design.md`, title: item.title, attached: "spec" },
    { kind: "plans", node: "plan", hook: "on.plan.requested", path: `${wt}/2026-09-12-${slug}.md`, title: `Plan: ${item.title}`, attached: "plan" },
    { kind: "reviews", node: "review", hook: "on.review.requested", path: `.engineering/reviews/2026-09-13-${slug}.md`, title: `Review: ${item.title}` },
    { kind: "sessions", node: "implement", hook: "on.implementation.start", path: `.engineering/sessions/${hex(901)}.md`, title: `Session ${hex(901)}` },
    { kind: "sessions", node: "verify", hook: "on.test.run", path: `.engineering/sessions/${hex(902)}.md`, title: hex(902) },
  ];
  // W11 · H: summaries from hooks that write no `# ` title, so the indexer titles them with an id.
  // Ahead of the generated sessions, so they are among the results search returns.
  if (long) docs.push(
    { kind: "sessions", node: "review", hook: "on.review.local.run", path: `.engineering/sessions/${hex(990)}.md`, title: hex(990), headingless: true },
    { kind: "sessions", node: "open_mr", hook: "on.mr.describe", path: `.engineering/sessions/${hex(991)}.md`, title: hex(991), headingless: true },
  );
  if (long) for (let i = 0; i < 12; i++) docs.push({ kind: "sessions", node: "implement", hook: "on.implementation.start", path: `.engineering/sessions/${hex(910 + i)}.md`, title: `Session ${hex(910 + i)}` });
  // W13 A: run info joined from the session. Artifacts and attachments have none;
  // summaries are attempt 1, and the long variant's implement sessions span rounds 0–2.
  let implementRun = 0;
  return docs.map((d, i) => ({
    document_id: hex(700 + i), work_item_id: item.id,
    repo: item.repo, title: d.title, kind: d.kind, source_kind: d.kind === "sessions" ? "session_summary" : "artifact",
    path: d.path, node_id: d.node, hook_point: d.hook,
    worker_session_id: d.kind === "sessions" ? hex(item.id.length + i) : null,
    attempt: d.kind === "sessions" ? 1 : null,
    round: d.kind !== "sessions" ? null : long && d.node === "implement" ? Math.min(2, Math.floor(implementRun++ / 5)) : 0,
    session_status: d.kind === "sessions" ? "done" : null,
    attachment_kind: d.attached ?? null,
    headingless: d.headingless ?? false,
    indexed_at: t(200 - i * 7),
  }));
}

/** A heading-less summary's opening, as the hook that wrote it would (W11 · H). */
const headinglessLead = (hook: string) =>
  hook === "on.mr.describe"
    ? "Opened the merge request description from the plan and the three task commits."
    : "Reviewed the invalidation path against main; the blob_sha check holds across a rebase but not a force-push.";

export function documentDetail(id: string, docs: any[]) {
  const d = docs.find((x) => x.document_id === id) ?? docs[0];
  // W12.2: no "Status · repo · path" lead line -- the pane header already says it.
  const body = `${d.headingless ? headinglessLead(d.hook_point) : `# ${d.title}`}

## Summary

${LONG_DESCRIPTION}

## Findings

| # | severity | file | message |
|---|----------|------|---------|
| 1 | critical | tests/test_fix_loop.py:212 | test_fix_loop_carries_findings fails |
| 2 | important | kraft/executor/fix_loop.py:4 | unused import |

\`\`\`python
def combine(plan, reports, commits):
    latest = max(reports or [0])
    named = max((task_from_subject(c) for c in commits), default=0)
    return max(latest, named)  # ${"a long comment that should scroll or wrap, not blow out the pane ".repeat(3)}
\`\`\`

${"Paragraph of prose. ".repeat(60)}
`;
  return {
    id: d.document_id, repo: d.repo, source_kind: d.source_kind, kind: d.kind, title: d.title, path: d.path,
    content: body, metadata: { author: "claude-opus-4-1", words: 1240 },
    source_created_at: t(10), source_updated_at: t(90), indexed_at: d.indexed_at,
    origin: d.source_kind === "artifact" ? "git_scan" : "event_ingest",
    links: [{ work_item_id: null, node_id: d.node_id, hook_point: d.hook_point, worker_session_id: d.worker_session_id }],
  };
}

export function artifactFor(item: any, variant: Variant) {
  return {
    work_item_id: item.id, path: item.gate_artifact ?? ".engineering/reviews/2026-09-13-caching-layer.md",
    title: `Review: ${item.title}`,
    content: documentDetail("", [{ document_id: "", repo: item.repo, source_kind: "artifact", kind: "reviews", title: `Review: ${item.title}`, path: item.gate_artifact, indexed_at: t(1), node_id: "review", hook_point: "on.review.requested", worker_session_id: null }]).content,
    truncated: variant === "long", artifact_max_bytes: 65536,
  };
}

/* ── settings & instance ─────────────────────────────────────────────────── */

export function settingsFor(variant: Variant, theme: { mode?: string; density?: string; group_by?: string }) {
  const long = variant === "long";
  const empty = variant === "empty";
  const paths = empty ? [] : long ? LONG_REPO_PATHS : REPO_PATHS;
  const repos = paths.map((p, i) => repo(p, i, long));
  const hooks: Record<string, any> = empty ? {} : {
    "on.env.prepare": { kind: "builtin", handler: "env_setup" },
    "on.spec.requested": { kind: "agent", command: "claude", steering: ["house-style"] },
    "on.plan.requested": { kind: "agent", command: "claude" },
    "on.implementation.start": { kind: "agent", command: "claude", interactive: false, timeout: 3600, steering: ["house-style", "commit-messages"] },
    "on.test.run": { kind: "subprocess", command: ["uv", "run", "pytest", "-q"], repos: Object.fromEntries(repos.map((r) => [r.path, { enabled: r.enabled, command: r.test_command }])) },
    "on.lint.run": { kind: "subprocess", command: long ? ["uv", "run", "ruff", "check", "--output-format=json", "--select=E,F,W,I,N,UP,B,A,C4,SIM", "kraft", "tests", "plugins"] : ["ruff", "check"] },
    "on.review.requested": { kind: "agent", command: "claude" },
    "on.mr.rebase": { kind: "forge", handler: "rebase" },
    "on.mr.open": { kind: "forge", handler: "open_mr" },
    "on.ci.poll": { kind: "forge", handler: "ci_poll" },
    ...(long ? { "on.security.scan": { kind: "subprocess", command: ["semgrep", "--config", "auto"] }, "on.test.fix": { kind: "agent", command: "claude" } } : {}),
  };
  const templates = empty ? [] : [
    { id: "default", nodes: DEFAULT_NODES, gates: 4 },
    { id: "quick-task", nodes: QUICK_NODES, gates: 0 },
    ...(long ? [{ id: "a-very-long-template-name-for-hotfixes-in-production", nodes: QUICK_NODES, gates: 0 }, { id: "docs-only", nodes: [QUICK_NODES[0]], gates: 0 }] : []),
  ];
  return {
    repos, hooks, templates,
    policy: {
      loops: { verify_fix_loop: { attempts: 3, wall_clock_s: 3600 }, review_fix_loop: { attempts: 2, wall_clock_s: 1800 }, ...(long ? { "an_extremely_long_loop_name_that_nobody_should_have_typed": { attempts: 9, wall_clock_s: 86400 } } : {}) },
      default: { attempts: 3, wall_clock_s: 3600 },
      findings: { loop_severities: ["critical", "important"] },
      budget: { work_item_usd: 5, daily_usd: long ? 250 : null },
      max_concurrent: 3, rate_limit_retries: 5,
    },
    theme: { palette: "nocturne", mode: theme.mode ?? "dark", density: theme.density ?? "compact", board: { group_by: theme.group_by ?? "status", show_done: 5, open_in: "peek" } },
    steering: { files: empty ? [] : [
      { name: "house-style", bytes: 1412 }, { name: "commit-messages", bytes: 388 },
      ...(long ? [{ name: "a-steering-file-with-a-very-long-name-that-will-not-fit-in-the-list-column", bytes: 8190 }, { name: "empty", bytes: 0 }, { name: "unknown-size", bytes: null }] : []),
    ], max_bytes: 8192 },
    steeringBody: (name: string) => ({ name, body: `# ${name}\n\n` + "- prefer stdlib over a dependency\n- never mock what you can run\n".repeat(long ? 40 : 4) }),
    intake: {
      enabled: variant !== "empty", interval_s: 300, priority_ceiling: 2,
      repos: repos.slice(0, 2).map((r) => r.path),
      repo_pickups: Object.fromEntries(repos.map((r, i) => [r.path, { items: i === 0 ? 14 : i === 1 ? 0 : null, last_picked_up: i === 0 ? t(400) : null }])),
      recent_pickups: variant === "empty" ? [] : Array.from({ length: long ? 25 : 5 }, (_, i) => ({
        work_item_id: hex(2000 + i), bead_id: `kraft-${hex(3000 + i).slice(0, 4)}`,
        title: i % 2 ? LONG_TITLES.capped : TITLES.capped, repo: repos[i % repos.length]?.path ?? null,
        priority: i % 3, status: ["created", "created", "skipped: no test_command", "created", "failed: bd unreachable"][i % 5], at: t(400 - i * 30),
      })),
    },
    access: { bind: variant === "empty" ? "127.0.0.1" : "0.0.0.0", port: 8765, session_expiry_days: 7, password_set: variant !== "empty", auth_required: variant !== "empty", allowed_hosts: long ? ["kraft.local", "10.0.0.12", "a-very-long-hostname.internal.acme-corporation.example.com"] : ["kraft.local"] },
    notify: { enabled: variant !== "empty", url_set: variant !== "empty", base_url: long ? "https://kraft.internal.acme-corporation.example.com:8765" : "http://kraft.local:8765", events: ["gate_requested", "work_item_needs_human", "work_item_completed"], last_test: variant === "empty" ? null : { at: t(500), status: long ? null : 200, ms: long ? null : 412, error: long ? "connect ETIMEDOUT 10.0.0.12:443 after 30000ms — the webhook host did not answer" : null } },
    sessions: { sessions: empty ? [] : [
      { id: hex(5000), label: "Chrome on macOS", ip: "127.0.0.1", created_at: t(0), last_seen_at: t(600), expires_at: t(7 * 1440), current: true },
      { id: hex(5001), label: long ? "Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/19.0 Mobile/15E148 Safari/604.1" : "Safari on iPhone", ip: "10.0.0.44", created_at: t(-3000), last_seen_at: t(100), expires_at: t(4000), current: false },
      ...(long ? Array.from({ length: 8 }, (_, i) => ({ id: hex(5010 + i), label: null, ip: `10.0.${i}.${i * 17}`, created_at: t(-i * 400), last_seen_at: t(i * 20), expires_at: t(5000), current: false })) : []),
    ] },
    health: { status: long ? "degraded" : "ok", invalid_templates: long ? { "docs-only": "node 'implement' references unknown task on.docs.write" } : {}, invalid_policy: long ? ["loops.an_extremely_long_loop_name_that_nobody_should_have_typed.wall_clock_s exceeds 24h"] : [], bind: "127.0.0.1", port: 8765, session_expiry_days: 7, version: "0.9.3" },
  };
}

export function analyticsFor(variant: Variant) {
  const long = variant === "long"; const empty = variant === "empty";
  const z = (n: number) => (empty ? 0 : n);
  return {
    totals: { work_items: z(long ? 1284 : 42), work_items_run: z(long ? 1201 : 39), by_status: { completed: z(30), abandoned: z(4), needs_human: z(3), active: z(5) }, mrs_merged: z(long ? 812 : 27), wall_ms: z(long ? 9.1e9 : 3.2e8), human_wait_ms: z(long ? 4.4e9 : 8.1e7), tokens_in: z(long ? 4.1e9 : 3.9e7), tokens_out: z(long ? 3.8e8 : 4.2e6), cost_usd: z(long ? 18432.19 : 212.4), cost_complete: !long, rounds: z(140), capped_out: z(6), completed: z(30), completed_prev: empty ? null : 22, median_lead_ms: z(long ? 2.9e8 : 5.6e6), human_wait_pct: z(long ? 48 : 25), fix_cycles: z(88), fix_cycles_capped: z(6), rejected_gates: z(9),
      // The backend always sends these (kraft/analytics.py, zero defaults); the fixture omitted them and Analytics threw on `.toFixed`.
      unplanned_touches_per_item: empty ? 0 : long ? 3.42 : 1.25, open_mr_to_green_ci_ms: z(long ? 5.4e6 : 1.2e6) },
    // Monday-keyed, newest last, like the server (analytics.py): the old keys were T0 minus
    // whole weeks (a Sunday), and `long ? 52 : 12 - i` put every long week at 52.
    weekly_merged: empty ? [] : (() => { const n = long ? 52 : 12; const d = new Date(T0); const mon = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() - ((d.getUTCDay() + 6) % 7)); return Array.from({ length: n }, (_, i) => ({ week_start: new Date(mon - (n - 1 - i) * 7 * 86400e3).toISOString().slice(0, 10), n: (i * 7) % 11 })); })(),
    by_node: empty ? [] : DEFAULT_NODES.map((n, i) => ({ node: n.id, runs: 30 + i * 4, wall_ms: 4e7 + i * 1e7, avg_ms: 1.2e6, tokens: 4e6 + i * 5e5, cost_usd: 21.5 + i * 3.1, cost_complete: i !== 4, rounds: 12, capped_out: i === 3 ? 6 : 0 })),
    by_repo: (empty ? [] : long ? LONG_REPO_PATHS : REPO_PATHS).map((p, i) => ({ repo: p, items: 14 - i, mrs: Math.max(0, 9 - i), tokens: 1.2e7, cost_usd: 70.2 - i * 5, cost_complete: true, done: Math.max(0, 9 - i), cycles: 30 })),
    rejected_gates_by_gate: empty ? [] : [{ gate: "spec_approval", n: 4 }, { gate: "plan_approval", n: 3 }, { gate: "code_review", n: 2 }],
    stop_reasons: empty ? [] : [{ label: "verify_fix_loop hit its cap", n: 6 }, { label: "spend cap reached", n: 2 }, { label: long ? "worktree refresh failed: fatal: Not possible to fast-forward, aborting (origin/main diverged after a force-push on the protected branch)" : "agent needs context", n: 1 }],
  };
}

export function searchFor(q: string, docs: any[], variant: Variant) {
  const long = variant === "long";
  return {
    query: q, mode: "hybrid",
    results: variant === "empty" ? [] : docs.slice(0, long ? 15 : 6).map((d, i) => ({
      id: d.document_id, repo: d.repo, source_kind: d.source_kind, kind: d.kind, title: d.title, path: d.path,
      snippet: long ? `…${LONG_DESCRIPTION.slice(40, 380)}…` : "…carry findings_measured from the verify node into the fix loop's system prompt…",
      score: 0.91 - i * 0.05,
      // The item the document belongs to, so opening a result lands on a real page (Kraft-hrlqs).
      links: [{ work_item_id: d.work_item_id, node_id: d.node_id, hook_point: d.hook_point, worker_session_id: d.worker_session_id }],
    })),
  };
}

/* ── the scenario ────────────────────────────────────────────────────────── */

export interface Scenario {
  variant: Variant;
  items: any[];                      // board (non-archived)
  archived: any[];
  byState: Record<DisplayState, ItemBundle>;
  bundles: Record<string, ItemBundle>;
  docs: Record<string, any[]>;
  settings: ReturnType<typeof settingsFor>;
  analytics: ReturnType<typeof analyticsFor>;
}

export function buildScenario(variant: Variant, theme: { mode?: string; density?: string; group_by?: string } = {}): Scenario {
  const bundles: Record<string, ItemBundle> = {};
  const byState = {} as Record<DisplayState, ItemBundle>;
  const items: any[] = []; const archived: any[] = [];
  const docs: Record<string, any[]> = {};
  if (variant !== "empty") {
    STATES.forEach((st, i) => {
      const b = buildItem(st, 11 + i, variant);
      byState[st] = b; bundles[b.item.id] = b; docs[b.item.id] = documentsFor(b.item, variant);
      (st === "archived" ? archived : items).push(b.item);
    });
    if (variant === "many") {
      for (let i = 0; i < 46; i++) {
        const st = STATES[i % (STATES.length - 1)];
        const b = buildItem(st, 100 + i, i % 5 === 0 ? "long" : "default");
        bundles[b.item.id] = b; docs[b.item.id] = documentsFor(b.item, "default");
        (st === "archived" ? archived : items).push(b.item);
      }
    }
  }
  return { variant, items, archived, byState, bundles, docs, settings: settingsFor(variant, theme), analytics: analyticsFor(variant) };
}
