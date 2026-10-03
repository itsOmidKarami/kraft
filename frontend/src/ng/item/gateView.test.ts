import { describe, expect, it, vi } from "vitest";
import type { ChainNode, KraftEvent, WorkerSession } from "../../types";
import { gateView } from "./gateView";
import { detail, V1 } from "./testkit";

const reviewed: ChainNode = { id: "final_review", kind: "gate", gate_after: null, tasks: ["final_review.review.auto_review"], steps: [["final_review.review.auto_review"]], reject_to: null };
const nodes = [...V1, reviewed];
const on = { doc: vi.fn(), reject: vi.fn() };

describe("gateView", () => {
  it("a pending gate: the diamond waits, the document opens, reject names the nearest earlier exec node", () => {
    const v = gateView(detail({ chain_definition: { template_id: "d", nodes }, pending_gate: "final_review", gate_artifact: ".x/review.md" }), reviewed, [], 0, { kind: "node", node: "final_review" }, on);
    expect(v.gate).toEqual({ id: "final_review", state: "current", sel: true });
    expect(v.doc?.label).toBe("review.md");
    expect(v.reject?.id).toBe("merge_request");
    expect(v.youSub).toBe("waiting");
    v.reject!.onClick!();
    expect(on.reject).toHaveBeenCalledWith("merge_request");
  });

  it("draws the auto_review task with its run state and the agent's verdict", () => {
    const s = { id: "s", hook_point: "final_review.review.auto_review", node_id: "final_review", status: "done", attempt: 1, round: 0, created_at: "t", wall_ms: 1 } as WorkerSession;
    const ev: KraftEvent = { seq: 1, work_item_id: "w1", type: "gate_approved", payload: { gate: "final_review", by: "agent" }, node_id: "final_review", created_at: "t" };
    // A passed gate keeps its document on disk, but the chip is for deciding: not drawn.
    const v = gateView(detail({ chain_definition: { template_id: "d", nodes }, worker_sessions: [s], gate_artifact: ".x/review.md" }), reviewed, [ev], 0, { kind: "chain" }, on);
    expect(v.reviewer).toMatchObject({ id: "auto_review", state: "done", chip: "approve", chipTone: "green" });
    expect(v.gate.state).toBe("done");
    expect(v.youSub).toBe("auto");
    expect(v.doc).toBeUndefined();
  });

  it("reads a gate's auto_review and message from the frozen chain, the API's chain listing no task on a gate (GR-4)", () => {
    const frozen = JSON.stringify({ chain: { nodes: [{ id: "plan", kind: "exec" }, { id: "plan_approval", kind: "gate", message: "Approve the plan.", auto_review: { id: "check", kind: "agent" } }] } });
    const s = { id: "s", hook_point: "plan_approval.auto_review", node_id: "plan_approval", status: "running", attempt: 1, round: 0, created_at: "t", started_at: "t" } as WorkerSession;
    const before = gateView(detail({ materialized_chain: frozen, current_node_id: "plan" }), V1[1], [], 0, { kind: "chain" }, on);
    expect(before.reviewer).toMatchObject({ id: "auto_review", state: "todo", chip: "runs first when reached" });
    expect(before.message).toBe("Approve the plan.");
    const running = gateView(detail({ materialized_chain: frozen, current_node_id: "plan_approval", worker_sessions: [s] }), V1[1], [], 0, { kind: "chain" }, on);
    expect(running.reviewer).toMatchObject({ id: "auto_review", state: "current" });
    expect(running.reviewer?.chip).toBeUndefined();
  });

  it("has no reviewer for a gate that declares none", () => {
    expect(gateView(detail(), V1[1], [], 0, { kind: "chain" }, on).reviewer).toBeUndefined();
  });
});
