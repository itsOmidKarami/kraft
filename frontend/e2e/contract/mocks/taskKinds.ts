/**
 * What kind a task is, read off an item's frozen chain (`materialized_chain`). The mock decides whether a session is an
 * agent's here, for the sample items that have a frozen chain, the doors and live motion: an agent session has a
 * harness, a model, usage and sub-agents; a builtin, subprocess or forge task has a wall time and the output of what it
 * ran (a test or lint command, a rebase, a forge call), and none of the rest (`TaskPane` says as much: a test
 * command names no harness and no model). The older sample items carry no frozen chain; their builder decides by
 * whether the row has a model.
 *
 * No imports: fixtures.ts, ngItems.ts, transitions.ts and motion.ts all read it.
 */

/** The model a mocked agent session runs on. */
export const MODEL = "claude-sonnet-5-5";
/** What `MODEL` costs, USD per million tokens of each kind (`src/kraft/prices.json`). */
export const PRICE = { tokens_in: 2, tokens_cache_write: 4, tokens_cache_read: 0.2, tokens_out: 10 };

type FrozenTask = { id: string; kind?: string; ref?: string | null; target?: string | null };
type FrozenStep = { id: string; tasks?: FrozenTask[] | null };
/** The shapes a frozen chain has (`kraft.templates`): a node lists `steps`, or its `tasks` directly (step `main`); an exec node
 *  may carry a fix loop and failure handlers of the same two shapes, a gate its reviewer, a group the nodes it holds. */
type FrozenNode = {
  id: string; steps?: FrozenStep[] | null; tasks?: FrozenTask[] | null; nodes?: FrozenNode[] | null;
  fix_loop?: { steps?: FrozenStep[] | null; tasks?: FrozenTask[] | null; judge?: FrozenTask | null } | null;
  on_failure?: { steps?: FrozenStep[] | null; tasks?: FrozenTask[] | null } | null;
  auto_review?: FrozenTask | null;
};

let last: { frozen: string; kinds: Map<string, string>; runs: Map<string, string>; own: Map<string, { path: string; kind: string }[]> } | null = null;

/** Each task's kind by hook point, and each node's own tasks in order: `<node>.<step>.<task>` (step `main` for a node that lists
 *  its tasks), `<node>.fix_loop.<step>.<task>` and `<node>.fix_loop.judge`, `<node>.on_failure.<step>.<task>`, a gate's
 *  `<gate>.auto_review`, a group's nodes under `<group>.`.
 *  shortcut: a task's or a step's own `on_failure` handler, a node's `on_merge_rejected` repair and its `on_base_changed.on_conflict` handler are not
 *  walked; their hook points read as unnamed, so an agent's, which is what every shipped one is. */
function read(frozen: string) {
  if (last?.frozen === frozen) return last;
  const kinds = new Map<string, string>();
  const runs = new Map<string, string>();
  const own = new Map<string, { path: string; kind: string }[]>();
  const homes = (prefix: string, h: { steps?: FrozenStep[] | null; tasks?: FrozenTask[] | null }) => {
    const out: { path: string; kind: string }[] = [];
    const add = (path: string, t: FrozenTask) => {
      out.push({ path, kind: t.kind ?? "agent" });
      const what = t.ref ?? t.target;
      if (what) runs.set(path, what);
    };
    for (const st of h.steps ?? []) for (const t of st.tasks ?? []) add(`${prefix}.${st.id}.${t.id}`, t);
    for (const t of h.tasks ?? []) add(`${prefix}.main.${t.id}`, t);
    for (const t of out) kinds.set(t.path, t.kind);
    return out;
  };
  const walk = (nodes: FrozenNode[], prefix: string) => {
    for (const n of nodes) {
      const id = `${prefix}${n.id}`;
      own.set(id, homes(id, n));
      if (n.fix_loop) {
        homes(`${id}.fix_loop`, n.fix_loop);
        if (n.fix_loop.judge) kinds.set(`${id}.fix_loop.judge`, n.fix_loop.judge.kind ?? "agent");
      }
      if (n.on_failure) homes(`${id}.on_failure`, n.on_failure);
      if (n.auto_review) kinds.set(`${id}.auto_review`, n.auto_review.kind ?? "agent");
      if (n.nodes) walk(n.nodes, `${id}.`);
    }
  };
  walk(JSON.parse(frozen)?.chain?.nodes ?? [], "");
  last = { frozen, kinds, runs, own };
  return last;
}

/** The kind of the task at `hookPoint`, or null where the chain does not name it (an item with no frozen chain, a steer's
 *  attempt on a node it never ran). */
export function taskKind(frozen: string | null | undefined, hookPoint: string): string | null {
  return frozen ? read(frozen).kinds.get(hookPoint) ?? null : null;
}

/** A node's own tasks in order, each with its kind (its steps' tasks, then those it lists directly); null where the frozen chain
 *  has no such node, `[]` for a gate. */
export function nodeTasks(frozen: string, node: string): { path: string; kind: string }[] | null {
  return read(frozen).own.get(node) ?? null;
}

/** Whether the session at `hookPoint` is an agent's: an escalation turn always; else its task's kind. A path the frozen chain
 *  does not name is an agent's (a steer's attempt on a node it never ran; a home of tasks the walk above leaves out); with no
 *  frozen chain at all, the row's own model says. */
export function isAgentSession(frozen: string | null | undefined, hookPoint: string, hasModel = false): boolean {
  if (hookPoint === "escalation") return true;
  const kind = taskKind(frozen, hookPoint);
  return kind !== null ? kind === "agent" : frozen ? true : hasModel;
}

/** What the task at `hookPoint` runs, as its frozen task names it: a builtin's `ref` (`kraft.mr_rebase`), a forge task's
 *  `target` (`mr.ci`). Null for an agent's task, and where the chain does not name the task. `RUNS` is keyed by it, so two
 *  tasks that share an id (`external_approval.main.await`, `post_merge_ci.main.await`) print each its own. */
export function taskRun(frozen: string | null | undefined, hookPoint: string): string | null {
  return frozen ? read(frozen).runs.get(hookPoint) ?? null : null;
}

/** What a non-agent task ran, by what runs (`taskRun`): the command (a test run; a rebase or a forge call has none), how long it
 *  takes, and what it prints once done, once failed, and while it runs. `mr` is the merge request the forge tasks talk about. */
export type Run = { command?: string; wall_ms: number; done: (mr: string) => string[]; failed?: (mr: string) => string[]; partial?: (mr: string) => string[] };
export const RUNS: Record<string, Run> = {
  // The changed-test-scope runner: one session per scope command it picked, this one the repo's tests (`test_command`).
  "kraft.verify_changed_test_scopes": {
    command: "uv run pytest -q", wall_ms: 210_000,
    done: () => ["........................................................ [ 48%]", "........................................................ [ 97%]", "...                                                      [100%]", "148 passed in 207.31s (0:03:27)"],
    failed: () => ["........F...................................................... [ 48%]", "FAILED tests/test_cache.py::test_cache_evicts_oldest - AssertionError", "1 failed, 147 passed in 204.18s (0:03:24)"],
    partial: () => ["........................................................ [ 48%]"],
  },
  "kraft.mr_rebase": {
    wall_ms: 600,
    done: () => ["nothing to rebase"],
    failed: () => ["CONFLICT (content): Merge conflict in search/cache.py", "CONFLICT (content): Merge conflict in search/reindex.py", "error: could not apply 4d1e2f3... Cache embeddings by content hash", "git rebase failed: resolve the conflicts, then retry"],
  },
  "mr.open_draft": {
    wall_ms: 8_000,
    done: (mr) => [`opened ${mr}`],
    // `forge.run`'s log of a failed forge call, `<task> failed: <the forge's answer>`: here the token's 403, on the route
    // the item's forge opens a merge request with (`adapters/forge/gh.py`'s pulls, `glab.py`'s merge_requests) and in
    // its words, as the transport renders it (`transport.py`). The merge request's address says which forge it is.
    failed: (mr) => [`draft_merge_request.open.open failed: ${mr.includes("/-/merge_requests/") ? "POST projects/:id/merge_requests: 403 Forbidden" : "POST repos/{owner}/{repo}/pulls: Resource not accessible by personal access token"} (HTTP 403)`],
  },
  // One session that goes `waiting` poll after poll: its wall time is every poll's.
  "mr.ci": {
    wall_ms: 480_000,
    done: (mr) => [`pipeline success: ${mr}`, ...["lint", "test (python 3.12)", "docs"].map((c) => `  ${c}: SUCCESS`)],
    failed: (mr) => [`pipeline failed: ${mr}`, "  lint: SUCCESS", "  test (python 3.12): FAILED", "  docs: SUCCESS"],
    partial: (mr) => [`pipeline pending: ${mr}`, ...["lint", "test (python 3.12)", "docs"].map((c) => `  ${c}: PENDING`)],
  },
  "mr.automated_review": { wall_ms: 200, done: () => ["automated review: no reviewer configured; nothing to collect"] },
  "mr.sync": { wall_ms: 3_000, done: (mr) => [`synced the description of ${mr}`] },
  "mr.mark_ready": { wall_ms: 2_000, done: (mr) => [`marked ${mr} ready for review`] },
  "mr.external_approval": { wall_ms: 1_000, done: (mr) => [`${mr}: approved`] },
  "mr.merge": { wall_ms: 9_000, done: (mr) => [`merged ${mr}`] },
  "mr.post_merge_ci": {
    wall_ms: 780_000,
    done: () => ["pipeline on main: success", ...["lint", "test (python 3.12)", "docs"].map((c) => `  ${c}: SUCCESS`)],
    partial: () => ["pipeline on main: pending", ...["lint", "test (python 3.12)", "docs"].map((c) => `  ${c}: PENDING`)],
  },
};
/** The other scope command a changed-test-scope pass runs in the sample: a docs check, beside the repo's tests. */
export const DOCS_SCOPE: Run = {
  command: "npx markdownlint-cli2 \"docs/**/*.md\"", wall_ms: 31_000,
  done: () => ["Linting: 42 file(s)", "Summary: 0 error(s)"],
  failed: () => ["Linting: 42 file(s)", "docs/search.md:12 MD040/fenced-code-language Fenced code blocks should have a language specified", "Summary: 1 error(s)"],
};

/** The forge waits (`ForgeAction.waits`, by target): every dispatch of one is an observation (`waits.observe`), a first
 *  look that settles at once included. */
export const WAITS = new Set(["mr.ci", "mr.automated_review", "mr.external_approval", "mr.merge", "mr.post_merge_ci", "ci.pipeline"]);
/** `running_time.running_s` (`caps.running_time`): the seconds at least one of the item's finished sessions ran, a forge
 *  wait's left out, so two that overlap count once. Shortcuts: a live session counts nothing yet (the real clock ends it at
 *  now; no card shows a live item's time), the clock does not restart at a person's retry (the sample retries none by
 *  hand), and a person's escalation turn is not dropped. */
export function runningS(frozen: string | null | undefined, sessions: any[]): number {
  const spans = sessions.filter((s) => s.wall_ms != null && !WAITS.has(taskRun(frozen, s.hook_point) ?? ""))
    .map((s) => [Date.parse(s.started_at ?? s.created_at), Date.parse(s.started_at ?? s.created_at) + s.wall_ms]).sort((a, b) => a[0] - b[0]);
  let total = 0, reach = -Infinity;
  for (const [a, z] of spans) { total += Math.max(0, z - Math.max(a, reach)); reach = Math.max(reach, z); }
  return Math.round(total / 1000);
}

/** `scope_runs` on an item's detail, as `board._scope_runs` lists them: one entry for each command a changed-test-scope task ran,
 *  in the order its sessions started. The node's scope pane draws from them: with none, a finished `test_changed_scopes` reads
 *  "0 of 0 scopes passed". An entry `had` already names (a seed's pass and scope) is kept, its outcome brought up to date. */
export function scopeRunsOf(frozen: string | null | undefined, sessions: any[], had: any[] = []): any[] {
  const known = new Map(had.map((r) => [r.session_id, r]));
  const runs = sessions
    .filter((s) => s.command && taskRun(frozen, s.hook_point) === "kraft.verify_changed_test_scopes")
    .map((s) => {
      const passed = s.status === "done" || s.status === "done_with_concerns" ? true : s.status === "failed" ? false : null;
      return {
        node_id: s.node_id, hook_point: s.hook_point, repository: s.repository ?? null, round: s.round, pass: s.pass ?? 1, command: s.command, identity: s.command,
        selected: true, scope: s.command === DOCS_SCOPE.command ? "docs/**" : "tests/**",
        ...known.get(s.id), session_id: s.id, passed, ...(passed === null ? {} : { exit_code: passed ? 0 : 1 }),
      };
    });
  const seen = new Map<string, number>();
  return runs.map((r) => {
    const at = `${r.pass}:${r.round}`;
    const order = seen.get(at) ?? 0;
    seen.set(at, order + 1);
    return { ...r, order: r.order ?? order };
  });
}

/** What `run` has printed when its session is in `status`: the command line, then its output so far, all of it once it is
 *  done, the failing run's once it failed. With no run (`label` is then what to call it), the session says its status. */
export function runOutput(run: Run | string | null | undefined, status: string, mr: string, label = String(run)): string[] {
  const r = typeof run === "string" ? RUNS[run] : run;
  if (!r) return [`${label} ${status}`];
  const lines = status === "failed" ? r.failed?.(mr) ?? r.done(mr) : status === "done" || status === "done_with_concerns" ? r.done(mr) : r.partial?.(mr) ?? [];
  return [...(r.command ? [r.command] : []), ...lines];
}

/** How long an agent's task typically runs and what it costs, by hook point (a real server's, rounded): `wall_s`, `cost_usd`,
 *  `tokens_out`. `tokens_in` is small, the rest of the prompt is cache. A task this does not list runs a minute for $0.30. */
export const AGENT_RUNS: Record<string, { wall_s: number; cost_usd: number; tokens_out: number }> = {
  "spec.main.author": { wall_s: 95, cost_usd: 0.6, tokens_out: 9_800 },
  "plan.main.author": { wall_s: 280, cost_usd: 2.1, tokens_out: 41_000 },
  "chain_revision.main.revise": { wall_s: 25, cost_usd: 0.29, tokens_out: 1_900 },
  "implementation.main.implement": { wall_s: 840, cost_usd: 2.6, tokens_out: 88_000 },
  "verification.review.code_review": { wall_s: 60, cost_usd: 0.4, tokens_out: 4_600 },
  "verification.fix_loop.main.repair": { wall_s: 240, cost_usd: 0.4, tokens_out: 12_000 },
  "verification.fix_loop.judge": { wall_s: 60, cost_usd: 0.26, tokens_out: 2_100 },
  "work_brief.main.author": { wall_s: 55, cost_usd: 0.31, tokens_out: 3_900 },
  "describe_merge_request.main.author": { wall_s: 100, cost_usd: 0.38, tokens_out: 5_200 },
  "work_item_summary.main.author": { wall_s: 65, cost_usd: 0.33, tokens_out: 4_400 },
  escalation: { wall_s: 360, cost_usd: 1.1, tokens_out: 21_000 },
};
/** A gate's reviewer (`<gate>.auto_review`) typically. */
export const REVIEWER_RUN = { wall_s: 75, cost_usd: 0.3, tokens_out: 2_800 };
export const agentRun = (hookPoint: string) => AGENT_RUNS[hookPoint] ?? (hookPoint.endsWith(".auto_review") ? REVIEWER_RUN : { wall_s: 60, cost_usd: 0.3, tokens_out: 3_000 });
