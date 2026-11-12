/**
 * What kind a task is, read off an item's frozen chain (`materialized_chain`): the one place the mock decides whether a
 * session is an agent's. An agent session has a harness, a model, usage and sub-agents; a builtin, subprocess or forge
 * task has a wall time and the output of what it ran (a test or lint command, a rebase, a forge call), and none of
 * the rest (`TaskPane` says as much: a test command names no harness and no model).
 *
 * No imports: fixtures.ts, ngItems.ts, transitions.ts and motion.ts all read it.
 */

/** The model a mocked agent session runs on. */
export const MODEL = "claude-sonnet-5-5";

type FrozenTask = { id: string; kind?: string };
type FrozenNode = { id: string; steps?: { id: string; tasks?: FrozenTask[] }[] | null; tasks?: FrozenTask[] | null };

let last: { frozen: string; kinds: Map<string, string> } | null = null;

/** Each task's kind by hook point, as the frozen chain names them: `<node>.<step>.<task>`, a node that lists its tasks
 *  with no steps keeping them under step `main`. */
function kindsOf(frozen: string): Map<string, string> {
  if (last?.frozen === frozen) return last.kinds;
  const kinds = new Map<string, string>();
  const nodes: FrozenNode[] = JSON.parse(frozen)?.chain?.nodes ?? [];
  for (const n of nodes) {
    for (const st of n.steps ?? []) for (const t of st.tasks ?? []) kinds.set(`${n.id}.${st.id}.${t.id}`, t.kind ?? "agent");
    for (const t of n.tasks ?? []) kinds.set(`${n.id}.main.${t.id}`, t.kind ?? "agent");
  }
  last = { frozen, kinds };
  return kinds;
}

/** The kind of the task at `hookPoint`, or null where the chain does not name it (an item with no frozen chain, a steer's
 *  attempt on a node it never ran). */
export function taskKind(frozen: string | null | undefined, hookPoint: string): string | null {
  return frozen ? kindsOf(frozen).get(hookPoint) ?? null : null;
}

/** Whether the session at `hookPoint` is an agent's: an escalation turn always; else its task's kind, and where the chain
 *  names none, whether the row reports a model (the older samples carry no frozen chain). */
export function isAgentSession(frozen: string | null | undefined, hookPoint: string, hasModel = false): boolean {
  if (hookPoint === "escalation") return true;
  const kind = taskKind(frozen, hookPoint);
  return kind === null ? hasModel : kind === "agent";
}

/** What a non-agent task ran, by its id: the command (a test or lint run; a rebase or a forge call has none), how long
 *  it takes, and what it prints. `mr` is the merge request the forge tasks talk about. */
export const RUNS: Record<string, { command?: string; wall_ms: number; output: (mr: string, status: string) => string[] }> = {
  unit_tests: {
    command: "uv run pytest -q", wall_ms: 210_000,
    output: (_mr, status) => status === "failed"
      ? ["........F...................................................... [ 48%]", "FAILED tests/test_fix_loop.py::test_fix_loop_carries_findings - AssertionError", "1 failed, 147 passed in 204.18s (0:03:24)"]
      : ["........................................................ [ 48%]", "........................................................ [ 97%]", "...                                                      [100%]", "148 passed in 207.31s (0:03:27)"],
  },
  lint: { command: "uv run ruff check .", wall_ms: 24_000, output: () => ["All checks passed!"] },
  typecheck: { command: "uv run pyright", wall_ms: 41_000, output: () => ["0 errors, 0 warnings, 0 informations"] },
  rebase: { wall_ms: 600, output: () => ["nothing to rebase"] },
  open_draft: {
    wall_ms: 8_000,
    output: (mr, status) => status === "failed" ? ["error: 403 Forbidden: the token lacks merge_request:write"] : [`opened ${mr}`],
  },
  mr_checks: {
    wall_ms: 480_000,
    output: (mr, status) => {
      const verdict = status === "done" ? "SUCCESS" : "PENDING";
      return [`pipeline ${status === "done" ? "success" : "pending"}: ${mr}`, ...["lint", "test (python 3.12)", "docs"].map((c) => `  ${c}: ${verdict}`)];
    },
  },
  release: { wall_ms: 3_000, output: (mr) => [`merged ${mr}`] },
};
