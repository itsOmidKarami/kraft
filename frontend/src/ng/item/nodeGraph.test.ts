import { describe, expect, it } from "vitest";
import type { ChainNode, WorkerSession } from "../../types";
import { footerState, nodeGraph } from "./nodeGraph";
import { detail } from "./testkit";

const NOW = Date.parse("2026-09-13T10:10:00Z");
const node: ChainNode = {
  id: "verification", kind: "exec", gate_after: null, fix_loop: "f", on_failure: ["verification.repair.repair_pass"],
  tasks: ["verification.checks.lint", "verification.checks.typecheck", "verification.review.code_review"],
  steps: [["verification.checks.lint", "verification.checks.typecheck"], ["verification.review.code_review"]],
};
let n = 0;
const s = (hook_point: string, over: Partial<WorkerSession> = {}) => ({ id: `s${n++}`, node_id: "verification", hook_point, status: "done", attempt: 1, round: 0, thread: 1, wall_ms: 240_000, model: null, created_at: `2026-09-13T09:0${n % 10}:00Z`, started_at: "2026-09-13T10:07:00Z", ...over }) as WorkerSession;

describe("nodeGraph", () => {
  it("draws each task's latest attempt: done with its duration, running with its elapsed, not started", () => {
    const item = detail({ worker_sessions: [
      s("verification.checks.lint", { attempt: 1 }), s("verification.checks.lint", { attempt: 2, round: 1, wall_ms: 11_000 }),
      s("verification.review.code_review", { status: "running", attempt: 2, round: 1, model: "sonnet", wall_ms: null }),
    ] });
    const g = nodeGraph(item, node, NOW);
    expect(g.steps.map((st) => [st.id, st.tasks.map((t) => [t.id, t.state, t.meta ?? null, t.attempt ?? null])])).toEqual([
      ["checks", [["lint", "done", "11s", 2], ["typecheck", "todo", null, null]]],
      ["review", [["code_review", "current", "running · 3m", 2]]],
    ]);
    expect(g.steps[1].tasks[0]).toMatchObject({ running: true, taskKind: "agent" });
    expect(g.loop).toEqual({ tone: "active", label: "fix loop · round 2" });
    expect(g.onFailure).toBe("repair_pass");
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
