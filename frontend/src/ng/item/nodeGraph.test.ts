import { describe, expect, it } from "vitest";
import type { ChainNode, SessionStatus, WorkerSession } from "../../types";
import { SESSION_STATUSES } from "../../types/vocab.generated";
import type { KraftEvent } from "../../types";
import { asOfPass, footerState, lookWord, loopIdle, loopRounds, nodeGraph, passesOf, passOf, passShown, passWhy, roundShown, sessionLook } from "./nodeGraph";
import { notStarted } from "./chainValues";
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

  it("draws a skipped task as skipped, whether the skip cut its session short or it never ran", () => {
    const item = detail({ skipped_paths: ["verification.checks"], worker_sessions: [s("verification.checks.lint", { status: "paused", skipped: true })] });
    const g = nodeGraph(item, node, NOW);
    expect(g.steps[0].tasks.map((t) => [t.id, t.state, t.meta, lookWord(t)])).toEqual([["lint", "done", "skipped", "skipped"], ["typecheck", "done", "skipped", "skipped"]]);
    expect(g.steps[1].tasks[0].state).toBe("todo");
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

  it("draws no loop for a node a budget stopped in its first round: nothing has looped", () => {
    const item = detail({ display_status: "needs_you", stop: { kind: "budget", node: "verification", resume_at: null, reason: null }, worker_sessions: [s("verification.checks.lint", { status: "failed" })] });
    expect(nodeGraph(item, node, NOW).loop).toBeUndefined();
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

describe("nodeGraph scope task state", () => {
  const solo: ChainNode = { id: "verification", kind: "exec", gate_after: null, tasks: [SCOPE_PATH], steps: [[SCOPE_PATH]] };
  it.each([
    ["failed when a scope failed, though a later one passed", ["failed", "done"], "failed"],
    ["running while a scope still runs, though a later one passed", ["running", "done"], "current"],
    ["done when every scope passed", ["done", "done"], "done"],
  ])("draws its box %s", (_n, [a, b], want) => {
    const item = scoped([scopeRun(null, "just test-a", 0, a), scopeRun(null, "just test-b", 0, b)]);
    expect(nodeGraph(item, solo, NOW).steps[0].tasks[0].state).toBe(want);
  });
});

describe("loopIdle", () => {
  const stop = (cycle: number): KraftEvent => ({ seq: 1, work_item_id: "w1", type: "judge_verdict", node_id: "verification", payload: { node_id: "verification", cycle, verdict: "stop_needs_human" }, created_at: "2026-09-13T09:30:00Z" }) as KraftEvent;
  it.each([
    // The node's own row for the newest round, and that a round which is over did not run it.
    ["repair", 2, { latest: 2, total: 3 }, [], "not yet"],
    ["repair", 3, { latest: 3, total: 3 }, [], "last round"],
    ["repair", 2, { latest: 2, total: 3 }, [stop(1)], "stopped by judge"],
    ["repair", 1, { latest: 2, total: 3 }, [], "not run in this round"],
    ["judge", 2, { latest: 2, total: 3 }, [], "not yet"],
    // The judge after the last round has no repair to rule on, and its row still says "not yet".
    ["judge", 3, { latest: 3, total: 3 }, [], "not yet"],
    ["judge", 2, { latest: 3, total: 3 }, [], "not run in this round"],
    ["judge", 1, { latest: 1, total: 3 }, [], "skipped · the first repair runs without the judge"],
  ] as const)("%s of round %s in %j: %s", (loop, round, rounds, events, want) => {
    expect(loopIdle(node, loop, round, rounds, [...events])).toBe(want);
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

describe("a node the chain ran again", () => {
  // The server numbers the passes: a reject, a retry or a base change sent the chain back to the node.
  const FIRST = LOOP_RUN.slice(0, 6).map((x) => ({ ...x, pass: 1 }));
  const PASSES = { verification: [{ pass: 1 }, { pass: 2, reason: "reject" as const, gate: "local_review" }] };
  const again = (round: number) => looped({ node_passes: PASSES, worker_sessions: [...FIRST, s("verification.checks.lint", { round, pass: 2, status: "running", wall_ms: null }), s("verification.escalation", { round: 0 })] });

  it("counts rounds from the pass the node is on, and draws that pass alone", () => {
    const retried = again(0);
    expect(loopRounds(retried, node)).toEqual({ latest: 1, total: 3 });
    expect(passOf(retried, "verification")).toHaveLength(1);
    const g = nodeGraph(retried, node, NOW);
    expect(states(g)).toEqual(["lint:current", "typecheck:todo", "code_review:todo"]);
    // One round and nothing run of the loop: no arc yet.
    expect(g.loop).toBeUndefined();
    expect(g.rounds).toMatchObject({ latest: 1, rows: [{ n: 1, tone: "warn" }] });
  });

  it("reads a pass that resumed at the round the one before left off at as its own, starting at that round", () => {
    // What a reject left before it gave the rounds back, and what an agent's retry still leaves: round 2 again.
    const resumed = again(1);
    expect(passOf(resumed, "verification").map((x) => x.pass)).toEqual([2]);
    expect(loopRounds(resumed, node)).toEqual({ first: 2, latest: 2, total: 3 });
    // Round 1 is no round of this pass: it is not listed, and picking it shows the newest.
    expect(nodeGraph(resumed, node, NOW, [], 1).rounds).toMatchObject({ selected: 2, rows: [{ n: 2 }] });
    expect(roundShown(resumed, node, 1)).toBe(2);
    // It has measured once and repaired nothing: no loop to draw yet, whatever its round is called.
    expect(nodeGraph(resumed, node, NOW, []).loop).toBeUndefined();
  });

  it("shows an earlier pass whole, with nothing of it in flight", () => {
    const item = again(0);
    const stopped = { ...item, display_status: "needs_you", stop: { kind: "cap" as const, node: "verification", resume_at: null, reason: null } } as typeof item;
    const first = asOfPass(stopped, "verification", 1);
    expect(passOf(first, "verification").map((x) => x.id)).toEqual(FIRST.map((x) => x.id));
    expect(loopRounds(first, node)).toEqual({ latest: 2, total: 3 });
    const g = nodeGraph(first, node, NOW, []);
    expect(states(g)).toEqual(["lint:failed", "typecheck:todo", "code_review:done"]);
    // The stop and the running node are the newest pass's: this one's last round is not red, nor amber.
    expect(g.loop!.tone).toBe("idle");
    expect(g.rounds!.rows.at(-1)).toMatchObject({ n: 2, tone: "ok" });
    expect(nodeGraph(asOfPass(again(0), "verification", 1), node, NOW, []).rounds!.rows.at(-1)).toMatchObject({ n: 2, tone: "ok", outcome: "done" });
    // The item's own state is untouched: it has started, it stands where it stands, and it stopped for what it stopped for.
    expect([first.current_node_id, first.stop, notStarted(first)]).toEqual(["verification", stopped.stop, false]);
    // Its escalation turns stay: they are the node's, in no pass.
    expect(g.side).toBeDefined();
    // The newest pass, a pass the node never had, and no pick are the item itself.
    for (const n of [2, 9, undefined]) expect(asOfPass(stopped, "verification", n)).toBe(stopped);
  });

  it.each([
    [undefined, 2], [1, 1], [2, 2], [3, 2],
  ])("shows the pass picked when the node has it, else the newest: %s → %s", (picked, want) => {
    expect(passShown(again(0), "verification", picked)).toBe(want);
  });

  it("names no pass for a node the chain ran once: nothing to tell apart", () => {
    expect(passesOf(looped(), "verification")).toEqual([]);
    expect(passShown(looped(), "verification", 1)).toBeUndefined();
  });

  it.each([
    [{ pass: 1 }, ""],
    [{ pass: 2, reason: "reject", gate: "local_review" }, "after a reject at local_review"],
    [{ pass: 2, reason: "retry" }, "after a retry"],
    [{ pass: 3, reason: "base_change" }, "after a base change"],
    [{ pass: 2 }, "started over"],
  ] as const)("says what started a pass: %j → %s", (p, want) => {
    expect(passWhy(p)).toBe(want);
  });
});

describe("a fix loop inside one pass", () => {

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

describe("sessionLook covers every session status", () => {
  it.each(SESSION_STATUSES)("%s has a look", (status) => {
    expect(sessionLook(s("verification.checks.lint", { status }), 0).state).toBeDefined();
  });
  it.each(["conflict", "infra", "infra_stop"] as const)("draws %s as failed", (status) => {
    expect(sessionLook(s("verification.checks.lint", { status }), 0).state).toBe("failed");
  });
  it("does not throw on a status a newer server sends", () => {
    expect(() => sessionLook(s("verification.checks.lint", { status: "brand_new" as SessionStatus }), 0)).not.toThrow();
  });
});
