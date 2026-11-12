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

type FrozenTask = { id: string; kind?: string };
type FrozenStep = { id: string; tasks?: FrozenTask[] | null };
/** The shapes a frozen chain has (`kraft.templates`): a node lists `steps`, or its `tasks` directly (step `main`); an exec node
 *  may carry a fix loop and failure handlers of the same two shapes, a gate its reviewer, a group the nodes it holds. */
type FrozenNode = {
  id: string; steps?: FrozenStep[] | null; tasks?: FrozenTask[] | null; nodes?: FrozenNode[] | null;
  fix_loop?: { steps?: FrozenStep[] | null; tasks?: FrozenTask[] | null; judge?: FrozenTask | null } | null;
  on_failure?: { steps?: FrozenStep[] | null; tasks?: FrozenTask[] | null } | null;
  auto_review?: FrozenTask | null;
};

let last: { frozen: string; kinds: Map<string, string>; own: Map<string, { path: string; kind: string }[]> } | null = null;

/** Each task's kind by hook point, and each node's own tasks in order: `<node>.<step>.<task>` (step `main` for a node that lists
 *  its tasks), `<node>.fix_loop.<step>.<task>` and `<node>.fix_loop.judge`, `<node>.on_failure.<step>.<task>`, a gate's
 *  `<gate>.auto_review`, a group's nodes under `<group>.`.
 *  shortcut: a task's or a step's own `on_failure` handler, a node's `on_merge_rejected` repair and its `on_base_changed.on_conflict` handler are not
 *  walked; their hook points read as unnamed, so an agent's, which is what every shipped one is. */
function read(frozen: string) {
  if (last?.frozen === frozen) return last;
  const kinds = new Map<string, string>();
  const own = new Map<string, { path: string; kind: string }[]>();
  const homes = (prefix: string, h: { steps?: FrozenStep[] | null; tasks?: FrozenTask[] | null }) => {
    const out: { path: string; kind: string }[] = [];
    for (const st of h.steps ?? []) for (const t of st.tasks ?? []) out.push({ path: `${prefix}.${st.id}.${t.id}`, kind: t.kind ?? "agent" });
    for (const t of h.tasks ?? []) out.push({ path: `${prefix}.main.${t.id}`, kind: t.kind ?? "agent" });
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
  last = { frozen, kinds, own };
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

/** What a non-agent task ran, by its id: the command (a test or lint run; a rebase or a forge call has none), how long it takes,
 *  and what it prints once done, once failed, and while it runs. `mr` is the merge request the forge tasks talk about. */
type Run = { command?: string; wall_ms: number; done: (mr: string) => string[]; failed?: (mr: string) => string[]; partial?: (mr: string) => string[] };
export const RUNS: Record<string, Run> = {
  unit_tests: {
    command: "uv run pytest -q", wall_ms: 210_000,
    done: () => ["........................................................ [ 48%]", "........................................................ [ 97%]", "...                                                      [100%]", "148 passed in 207.31s (0:03:27)"],
    failed: () => ["........F...................................................... [ 48%]", "FAILED tests/test_fix_loop.py::test_fix_loop_carries_findings - AssertionError", "1 failed, 147 passed in 204.18s (0:03:24)"],
    partial: () => ["........................................................ [ 48%]"],
  },
  lint: {
    command: "uv run ruff check .", wall_ms: 24_000,
    done: () => ["All checks passed!"],
    failed: () => ["src/kraft/executor/fix_loop.py:4:8: F401 `Optional` imported but unused", "Found 1 error."],
  },
  typecheck: {
    command: "uv run pyright", wall_ms: 41_000,
    done: () => ["0 errors, 0 warnings, 0 informations"],
    failed: () => ["src/kraft/executor/fix_loop.py:88:12 - error: Argument of type None cannot be assigned to parameter plan", "1 error, 0 warnings, 0 informations"],
  },
  rebase: {
    wall_ms: 600,
    done: () => ["nothing to rebase"],
    failed: () => ["CONFLICT (content): Merge conflict in search/cache.py", "CONFLICT (content): Merge conflict in search/reindex.py", "error: could not apply 4d1e2f3... Cache embeddings by content hash", "git rebase failed: resolve the conflicts, then retry"],
  },
  open_draft: {
    wall_ms: 8_000,
    done: (mr) => [`opened ${mr}`],
    failed: () => ["error: 403 Forbidden: the token lacks merge_request:write"],
  },
  mr_checks: {
    wall_ms: 480_000,
    done: (mr) => [`pipeline success: ${mr}`, ...["lint", "test (python 3.12)", "docs"].map((c) => `  ${c}: SUCCESS`)],
    failed: (mr) => [`pipeline failed: ${mr}`, "  lint: SUCCESS", "  test (python 3.12): FAILED", "  docs: SUCCESS"],
    partial: (mr) => [`pipeline pending: ${mr}`, ...["lint", "test (python 3.12)", "docs"].map((c) => `  ${c}: PENDING`)],
  },
  release: { wall_ms: 3_000, done: (mr) => [`merged ${mr}`] },
};

/** What `task` has printed when its session is in `status`: the command line, then its output so far, all of it once it is done, the
 *  failing run's once it failed. A task `RUNS` does not list says its status. */
export function runOutput(task: string, status: string, mr: string): string[] {
  const run = RUNS[task];
  if (!run) return [`${task} ${status}`];
  const lines = status === "failed" ? run.failed?.(mr) ?? run.done(mr) : status === "done" || status === "done_with_concerns" ? run.done(mr) : run.partial?.(mr) ?? [];
  return [...(run.command ? [run.command] : []), ...lines];
}
