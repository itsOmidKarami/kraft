import { describe, expect, it } from "vitest";
import type { ChainNode, WorkerSession } from "../../types";
import type { KraftEvent } from "../../types";
import { footerState, loopRounds, nodeGraph, passOf } from "./nodeGraph";
import { detail, FROZEN, LOOPED, SCOPE_PATH, scopeRun, scoped } from "./testkit";

const NOW = Date.parse("2026-09-13T10:10:00Z");
const node: ChainNode = {
  id: "verification", kind: "exec", gate_after: null, fix_loop: "f", on_failure: ["verification.repair.repair_pass"],
  tasks: ["verification.checks.lint", "verification.checks.typecheck", "verification.review.code_review"],
  steps: [["verification.checks.lint", "verification.checks.typecheck"], ["verification.review.code_review"]],
};
let n = 0;
const s = (hook_point: string, over: Partial<WorkerSession> = {}) => ({ id: `s${n++}`, node_id: "verification", hook_point, status: "done", attempt: 1, round: 0, thread: 1, wall_ms: 240_000, model: null, created_at: `2026-09-13T09:${String(n).padStart(2, "0")}:00Z`, started_at: "2026-09-13T10:07:00Z", ...over }) as WorkerSession;

describe("nodeGraph", () => {
  it("draws each task's latest attempt: done with its duration, running with its elapsed, not started", () => {
    const item = detail({ worker_sessions: [
      s("verification.checks.lint", { attempt: 1 }), s("verification.checks.lint", { attempt: 2, round: 1, wall_ms: 11_000 }),
      s("verification.review.code_review", { status: "running", attempt: 2, round: 1, model: "sonnet", wall_ms: null }),
    ] });
    const g = nodeGraph(item, node, NOW);
    expect(g.steps.map((st) => [st.id, st.tasks.map((t) => [t.id, t.state, t.meta ?? null, t.attempt ?? null])])).toEqual([
      // A fix-loop node names the round, not a run count: no ×N on its tasks.
      ["checks", [["lint", "done", "11s", null], ["typecheck", "todo", null, null]]],
      ["review", [["code_review", "current", "running · 3m", null]]],
    ]);
    expect(g.steps[1].tasks[0]).toMatchObject({ running: true, taskKind: "agent" });
    expect(g.loop).toEqual({ tone: "active", label: "round 2", tasks: [] });
    expect(g.onFailure).toBe("repair_pass");
  });

  it("draws a task by its kind in the frozen chain, before it has run (WI-3)", () => {
    const g = nodeGraph(detail({ materialized_chain: FROZEN }), node, NOW);
    expect(g.steps.map((st) => st.tasks.map((t) => [t.id, t.taskKind]))).toEqual([[["lint", "subprocess"], ["typecheck", undefined]], [["code_review", "agent"]]]);
  });

  it("hangs the escalation on a side branch with its thread and turn, and a red loop when capped", () => {
    const item = detail({ display_status: "needs_you", stop: { kind: "cap", node: "verification", resume_at: null, reason: null }, worker_sessions: [
      s("verification.checks.lint", { round: 2, status: "capped_out" }),
      s("escalation", { thread: 1 }), s("escalation", { thread: 1, status: "needs_context" }),
    ] });
    const g = nodeGraph(item, node, NOW);
    expect(g.side).toMatchObject({ id: "escalation", meta: "thread 1 · turn 2", state: "current" });
    expect(g.loop?.tone).toBe("red");
    expect(g.steps[0].tasks[0]).toMatchObject({ state: "failed", attemptStopped: true });
  });

  it("has no loop before a round ran, and no side branch without an escalation", () => {
    const g = nodeGraph(detail({ worker_sessions: [s("verification.checks.lint")] }), node, NOW);
    expect(g.loop).toBeUndefined();
    expect(g.side).toBeUndefined();
  });
});

const verdict = (cycle: number, v: string) => ({ type: "judge_verdict", payload: { node_id: "verification", cycle, verdict: v } }) as unknown as KraftEvent;
/** Round 1 failed, the repair ran (310s), round 2 failed, the judge said continue after it, and the second repair runs. */
const LOOP_RUN = [
  s("verification.checks.lint", { round: 0, status: "failed" }), s("verification.review.code_review", { round: 0 }),
  s("verification.fix_loop.main.repair", { round: 1, wall_ms: 310_000, model: "sonnet" }),
  s("verification.checks.lint", { round: 1, status: "failed" }), s("verification.review.code_review", { round: 1, wall_ms: 90_000 }),
  s("verification.fix_loop.judge", { round: 1, wall_ms: 31_000, model: "sonnet" }),
  s("verification.fix_loop.main.repair", { round: 2, status: "running", model: "sonnet", wall_ms: null, attempt: 2 }),
];
const looped = (over = {}) => detail({ materialized_chain: LOOPED, worker_sessions: LOOP_RUN, ...over });
const states = (g: ReturnType<typeof nodeGraph>) => g.steps.flatMap((st) => st.tasks.map((t) => `${t.id}:${t.state}`));

describe("nodeGraph rounds", () => {
  it("draws the newest round, with the repair that is leading out of it and the judge that came after it", () => {
    const g = nodeGraph(looped(), node, NOW, [verdict(1, "continue")]);
    expect(states(g)).toEqual(["lint:failed", "typecheck:todo", "code_review:done"]);
    expect(g.loop).toMatchObject({ tone: "active", label: "round 2 of 3" });
    expect(g.loop!.tasks.map((t) => [t.label, t.state, t.meta, t.faded ?? false, t.attempt ?? null])).toEqual([
      ["repair", "current", "running · 3m", false, null],
      ["judge", "done", "continue", false, null],
    ]);
    expect(g.loop!.tasks.map((t) => t.icon ?? null)).toEqual([null, "scale"]);
    // Selected as `<step>.<task>` under `fix_loop` (a bare `tasks:` list is the step `main`), drawn as the task.
    expect(g.loop!.tasks.map((t) => [t.id, t.label])).toEqual([["main.repair", "repair"], ["judge", "judge"]]);
    expect(g.rounds).toMatchObject({ selected: 2, latest: 2, total: 3 });
    expect(g.rounds!.rows).toEqual([{ n: 1, tone: "bad", outcome: "sent to the fix loop" }, { n: 2, tone: "warn", outcome: "running" }].map((r) => expect.objectContaining(r)));
  });

  it("draws an earlier round whole: its tasks, its repair, and a judge that skipped the first one", () => {
    const g = nodeGraph(looped(), node, NOW, [verdict(1, "continue")], 1);
    expect(g.steps[0].tasks[0]).toMatchObject({ id: "lint", state: "failed" });
    expect(g.steps[1].tasks[0]).toMatchObject({ id: "code_review", state: "done", meta: "4m" });
    expect(g.loop!.label).toBe("round 1 of 3");
    expect(g.loop!.tasks.map((t) => [t.label, t.state, t.meta, t.faded ?? false])).toEqual([
      ["repair", "done", "done · 5m", false],
      ["judge", "todo", "skipped · first repair", true],
    ]);
    expect(g.rounds!.selected).toBe(1);
  });

  it("ignores a pick the item has not reached, and a repair that has not run is 'not yet' or 'last round'", () => {
    const only1 = looped({ worker_sessions: LOOP_RUN.slice(0, 2) });
    const g = nodeGraph(only1, node, NOW, [], 3);
    expect(g.rounds!.selected).toBe(1);
    expect(g.loop).toBeUndefined();
    const idle = nodeGraph(looped({ worker_sessions: LOOP_RUN.slice(0, 5) }), node, NOW, []);
    expect(idle.loop!.tasks.map((t) => [t.label, t.meta])).toEqual([["repair", "not yet"], ["judge", "not yet"]]);
    const lastRound = nodeGraph(looped({ worker_sessions: [...LOOP_RUN.slice(0, 7), s("verification.checks.lint", { round: 2 })] }), node, NOW, [], 3);
    expect(lastRound.loop!.tasks[0]).toMatchObject({ label: "repair", meta: "last round" });
  });

  it("draws a judge that stopped the loop red, and the repair it stopped 'stopped by judge'", () => {
    const g = nodeGraph(looped({ worker_sessions: LOOP_RUN.slice(0, 6) }), node, NOW, [verdict(1, "stop_needs_human")]);
    expect(g.loop!.tasks.map((t) => [t.label, t.state, t.meta])).toEqual([["repair", "todo", "stopped by judge"], ["judge", "failed", "stop"]]);
  });

  it("calls the newest round running while the node is the one the run stands on, and done once it has moved on", () => {
    const running = nodeGraph(looped(), node, NOW, []);
    expect(running.rounds!.rows.map((r) => [r.n, r.tone, r.outcome])).toEqual([[1, "bad", "sent to the fix loop"], [2, "warn", "running"]]);
    const moved = nodeGraph(looped({ current_node_id: "merge_request" }), node, NOW, []);
    expect(moved.rounds!.rows.map((r) => [r.n, r.tone, r.outcome])).toEqual([[1, "bad", "sent to the fix loop"], [2, "ok", "done"]]);
    // The arc is amber only while the loop runs on past its first round, and red when it stopped.
    expect(running.loop!.tone).toBe("active");
    expect(moved.loop!.tone).toBe("idle");
  });

  it("draws a loop written as steps with every task of every step, each at its own path", () => {
    const frozen = JSON.parse(LOOPED);
    frozen.chain.nodes[2].fix_loop = { max_attempts: 2, steps: [{ id: "repair", tasks: [{ id: "repair", kind: "agent" }] }, { id: "sync", tasks: [{ id: "sync", kind: "builtin" }] }], judge: { id: "judge", kind: "agent" } };
    const sessions = [...LOOP_RUN.slice(0, 2), s("verification.fix_loop.repair.repair", { round: 1 }), s("verification.fix_loop.sync.sync", { round: 1 }), s("verification.checks.lint", { round: 1 }), s("verification.review.code_review", { round: 1 })];
    const g = nodeGraph(looped({ materialized_chain: JSON.stringify(frozen), worker_sessions: sessions }), node, NOW, [], 1);
    expect(g.loop!.tasks.map((t) => [t.id, t.label, t.state])).toEqual([["repair.repair", "repair", "done"], ["sync.sync", "sync", "done"], ["judge", "judge", "todo"]]);
  });

  it("marks the newest round stopped, in red, when the item stopped on the node", () => {
    const g = nodeGraph(looped({ worker_sessions: LOOP_RUN.slice(0, 6), display_status: "needs_you", stop: { kind: "stuck", node: "verification", resume_at: null, reason: null } }), node, NOW, []);
    expect(g.rounds!.rows.at(-1)).toMatchObject({ n: 2, tone: "bad", outcome: "stopped · needs you" });
  });
});

describe("nodeGraph scope task", () => {
  it("puts no ×N on a changed-test-scope task: its sessions are scopes, not attempts", () => {
    const solo: ChainNode = { id: "verification", kind: "exec", gate_after: null, tasks: [SCOPE_PATH], steps: [[SCOPE_PATH]] };
    const item = scoped([scopeRun(null, "just test-a", 0, "done"), scopeRun(null, "just test-b", 0, "done"), scopeRun(null, "just test-c", 0, "done")]);
    // Three sessions at one hook point: the column says attempt 3, the box must not.
    expect(item.worker_sessions.map((x) => x.attempt)).toEqual([1, 1, 1]);
    const counted = { ...item, worker_sessions: item.worker_sessions.map((x, i) => ({ ...x, attempt: i + 1 })) };
    expect(nodeGraph(counted, solo, NOW).steps[0].tasks[0].attempt).toBeUndefined();
  });
});

describe("footerState", () => {
  it.each([
    ["running", [{ status: "running" }], "running"],
    ["paused", [{ status: "paused" }], "paused"],
    ["needs_you", [{ status: "done" }], "stopped"],
    ["failed", [{ status: "failed" }], "stopped"],
    ["failed", [{ status: "running" }], "stopped"],
    ["running", [], null],
    ["cancelled", [{ status: "done" }], null],
  ] as const)("%s with %j → %s", (display_status, sessions, want) => {
    expect(footerState(detail({ display_status }), sessions.map((x) => s("verification.checks.lint", x as never)))).toBe(want);
  });
});

describe("a fix loop that started over", () => {
  // A retry (or a base change) restarts the loop at round 0: the node's earlier pass is history.
  const retried = () => looped({ worker_sessions: [...LOOP_RUN, s("verification.checks.lint", { round: 0, status: "running", wall_ms: null }), s("verification.escalation", { round: 0 })] });

  it("counts rounds from the pass the node is on, and draws that pass alone", () => {
    expect(loopRounds(retried(), node)).toEqual({ latest: 1, total: 3 });
    expect(passOf(retried(), "verification")).toHaveLength(1);
    const g = nodeGraph(retried(), node, NOW);
    expect(states(g)).toEqual(["lint:current", "typecheck:todo", "code_review:todo"]);
    // One round and nothing run of the loop: no arc yet.
    expect(g.loop).toBeUndefined();
    expect(g.rounds).toMatchObject({ latest: 1, rows: [{ n: 1, tone: "warn" }] });
  });

  it("is not told apart by an escalation turn, which carries round 0 whenever it comes", () => {
    const escalated = looped({ worker_sessions: [...LOOP_RUN, s("verification.escalation", { round: 0 })] });
    expect(loopRounds(escalated, node)).toEqual({ latest: 2, total: 3 });
  });

  it("shows the re-measure after on_failure (round -1) in the round it followed, the latest run of the task", () => {
    const at = (round: number) => [s("verification.checks.lint", { round, status: "failed" }), s("verification.checks.lint", { round: -1, attempt: 2 })];
    // The first round's, on a fresh entry; a later round's when the node was entered again with the counter at 2.
    const first = looped({ worker_sessions: at(0) });
    expect(loopRounds(first, node)).toEqual({ latest: 1, total: 3 });
    expect(states(nodeGraph(first, node, NOW))[0]).toBe("lint:done");
    const again = looped({ worker_sessions: [...LOOP_RUN.slice(0, 5), ...at(2)] });
    expect(loopRounds(again, node)).toEqual({ latest: 3, total: 3 });
    expect(states(nodeGraph(again, node, NOW, undefined, 2))[0]).toBe("lint:failed");
    expect(states(nodeGraph(again, node, NOW))[0]).toBe("lint:done");
  });

  it("keeps the rounds before a fix cycle that was paused and refunded, which measures a round it had already measured", () => {
    const resumed = looped({ worker_sessions: [...LOOP_RUN, s("verification.checks.lint", { round: 1, attempt: 3, status: "running", wall_ms: null })] });
    expect(loopRounds(resumed, node)).toEqual({ latest: 2, total: 3 });
    expect(passOf(resumed, "verification")).toHaveLength(LOOP_RUN.length + 1);
  });
});
