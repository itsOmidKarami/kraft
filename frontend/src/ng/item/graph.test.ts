import { describe, expect, it } from "vitest";
import type { KraftEvent, WorkerSession } from "../../types";
import { accessibleName } from "../graph/types";
import { chainGraph, rejectTarget } from "./graph";
import { detail, V1 } from "./testkit";

const NOW = Date.parse("2026-09-13T10:10:00Z");
const sess = (node_id: string, over: Partial<WorkerSession> = {}) => ({ id: `${node_id}${over.attempt ?? 1}${over.hook_point ?? ""}`, node_id, hook_point: `${node_id}.x.y`, status: "done", attempt: 1, round: 0, started_at: "2026-09-13T09:00:00Z", ...over }) as WorkerSession;
const approved = (gate: string, by: string): KraftEvent => ({ seq: 1, work_item_id: "w1", type: "gate_approved", payload: { gate, by }, node_id: gate, created_at: "t" });

describe("chainGraph", () => {
  it("marks done, current and not started from the current node, with durations and who passed each gate", () => {
    const item = detail({
      worker_sessions: [sess("plan"), sess("verification", { attempt: 2, round: 1, status: "running", started_at: "2026-09-13T10:07:00Z" }), sess("verification", { started_at: "2026-09-13T09:30:00Z" })],
      usage: { total: {} as never, by_node: [{ node: "plan", wall_ms: 6 * 60_000 } as never] },
      summary: { nodes_done: 2, nodes_total: 4, gates_passed: 1, step: { index: 2, count: 2 } },
    });
    const { nodes } = chainGraph(item, [approved("plan_approval", "human")], NOW);
    expect(nodes.map((n) => [n.id, n.state, n.meta ?? n.sub ?? null])).toEqual([
      ["plan", "done", "6m"],
      ["plan_approval", "done", "you"],
      // The attempt in flight, not the node's first session.
      ["verification", "current", "2/2 · 3m"],
      ["merge_request", "todo", null],
    ]);
    expect(nodes[2]).toMatchObject({ running: true, attempt: 2, kind: "exec" });
    expect(nodes[1].kind).toBe("gate");
    expect(chainGraph(item, [approved("plan_approval", "agent")], NOW).nodes[1].meta).toBe("auto");
  });

  // R12b-09: the accessible name said "running" for every one of these but the running row.
  it.each([
    ["failed", { kind: "failed", node: "verification" }, { state: "failed", meta: "failed", metaTone: "red" }, "verification, node, failed"],
    ["needs_you", { kind: "cap", node: "verification" }, { state: "current", capped: true, attemptStopped: true, meta: "capped" }, "verification, node, capped"],
    ["paused", null, { state: "current", paused: true, sub: "paused" }, "verification, node, paused"],
    ["needs_you", { kind: "question", node: "verification" }, { state: "current", sub: "needs you" }, "verification, node, needs you"],
    ["needs_you", { kind: "stuck", node: "verification" }, { state: "current", sub: "needs you" }, "verification, node, needs you"],
    ["waiting", { kind: "rate_limit", node: "verification" }, { state: "current", running: false, wait: "waiting · rate limit" }, "verification, node, waiting · rate limit"],
    ["waiting", { kind: "wait", node: "verification" }, { state: "current", running: false, wait: "waiting on CI" }, "verification, node, waiting on CI"],
    ["running", null, { state: "current", running: true }, "verification, node, running"],
  ])("draws the current node of a %s item, and names it so", (display_status, stop, want, name) => {
    const item = detail({ display_status: display_status as never, stop: stop && ({ ...stop, resume_at: null, reason: null } as never) });
    const node = chainGraph(item, [], NOW).nodes[2];
    expect(node).toMatchObject(want);
    expect(accessibleName(node, "node")).toBe(name);
  });

  it("draws a gate waiting for you as the current gate (the amber diamond)", () => {
    const item = detail({ current_node_id: "plan_approval", display_status: "needs_you", stop: { kind: "gate", node: "plan_approval", resume_at: null, reason: null } });
    const gate = chainGraph(item, [], NOW).nodes[1];
    expect(gate).toMatchObject({ kind: "gate", state: "current", sub: "needs you" });
    expect(accessibleName(gate, "gate")).toBe("plan_approval, gate, needs you");
  });

  it("hides the escalation badge on a capped node, shows it elsewhere", () => {
    const esc = sess("verification", { hook_point: "escalation" });
    const capped = detail({ display_status: "needs_you", stop: { kind: "cap", node: "verification", resume_at: null, reason: null }, worker_sessions: [esc] });
    expect(chainGraph(capped, [], NOW).nodes[2].esc).toBe(false);
    expect(chainGraph(detail({ worker_sessions: [esc] }), [], NOW).nodes[2].esc).toBe(true);
  });

  it("finishes every node of a done item", () => {
    expect(chainGraph(detail({ display_status: "done", current_node_id: null }), [], NOW).nodes.every((n) => n.state === "done")).toBe(true);
  });

  it("draws the fix-loop arc once a round has run, red when capped, and the reject arc only while its gate is selected", () => {
    const nodes = V1.map((n) => (n.id === "verification" ? { ...n, fix_loop: "f" } : n));
    const base = { chain_definition: { template_id: "d", nodes }, worker_sessions: [sess("verification", { round: 1 })] };
    expect(chainGraph(detail(base), [], NOW).arcs()).toEqual([{ kind: "loop", node: "verification", tone: "active", label: "round 2" }]);
    const capped = detail({ ...base, display_status: "needs_you", stop: { kind: "cap", node: "verification", resume_at: null, reason: null } });
    expect(chainGraph(capped, [], NOW).arcs()[0]).toMatchObject({ tone: "red" });
    expect(chainGraph(detail(base), [], NOW).arcs("plan_approval")).toContainEqual({ kind: "reject", from: "plan_approval", to: "plan" });
    // An exec node has no reject arc, even with an exec node before it.
    expect(chainGraph(detail(base), [], NOW).arcs("verification").some((a) => a.kind === "reject")).toBe(false);
    // A fix-loop node that has not looped yet draws no arc.
    expect(chainGraph(detail({ ...base, worker_sessions: [sess("verification")] }), [], NOW).arcs()).toEqual([]);
  });
});

describe("rejectTarget", () => {
  it("is the gate's reject_to, else the nearest earlier exec node (R23)", () => {
    expect(rejectTarget(V1, "plan_approval")).toBe("plan");
    const bare = V1.map((n) => (n.id === "plan_approval" ? { ...n, reject_to: null } : n));
    expect(rejectTarget(bare, "plan_approval")).toBe("plan");
    expect(rejectTarget([{ id: "g", kind: "gate", gate_after: null, tasks: [] }], "g")).toBeNull();
  });
});
