import { describe, expect, it, vi } from "vitest";
import type { ChainNode, GateRound, WorkerSession } from "../../types";
import { gateView } from "./gateView";
import { detail, SLICED, V1 } from "./testkit";

const reviewed: ChainNode = { id: "final_review", kind: "gate", gate_after: null, tasks: ["final_review.review.auto_review"], steps: [["final_review.review.auto_review"]], reject_to: null };
const nodes = [...V1, reviewed];
const on = { doc: vi.fn(), reject: vi.fn() };
const round = (n: number, over: Partial<GateRound> = {}): GateRound => ({ n, requested_at: "t", ended_at: "t", outcome: "approved", by: "assistant", note: null, sent_to: null, reopened: false, reviewer: null, ...over });
// plan_approval asked, sent back to plan with a note, asked again, approved: what 510ysadf did.
const NOTE = "The plan skips the invalidation story. ".repeat(18).trim();
const twice = [round(1, { outcome: "rejected", note: NOTE, sent_to: "plan" }), round(2)];
const asked = (...rounds: GateRound[]) => detail({ gate_rounds: { plan_approval: rounds }, current_node_id: "plan", gate_artifact: ".x/plan.md" });

describe("gateView", () => {
  it("a pending gate: the diamond waits, the document opens, reject names the nearest earlier exec node", () => {
    const v = gateView(detail({ chain_definition: { template_id: "d", nodes }, pending_gate: "final_review", gate_artifact: ".x/review.md" }), reviewed, 0, { kind: "node", node: "final_review" }, on);
    expect(v.gate).toEqual({ id: "final_review", state: "current", sel: true });
    expect(v.doc?.label).toBe("review.md");
    expect(v.reject?.id).toBe("merge_request");
    expect(v.reject?.label).toBe("merge request");
    expect(v.youSub).toBe("waiting");
    v.reject!.onClick!();
    expect(on.reject).toHaveBeenCalledWith("merge_request");
  });

  it("a skipped gate says so under the diamond, not that it decides after the agent (R14b-05)", () => {
    const v = gateView(detail({ chain_definition: { template_id: "d", nodes }, gate_rounds: { final_review: [round(1, { outcome: "skipped", by: null })] } }), reviewed, 0, { kind: "chain" }, on);
    expect(v.youSub).toBe("skipped");
  });

  it("draws the auto_review task with its run state and the agent's verdict", () => {
    const s = { id: "s", hook_point: "final_review.review.auto_review", node_id: "final_review", status: "done", attempt: 1, round: 0, created_at: "t", wall_ms: 1 } as WorkerSession;
    // A passed gate keeps its document on disk, but the chip is for deciding: not drawn.
    const v = gateView(detail({ chain_definition: { template_id: "d", nodes }, worker_sessions: [s], gate_artifact: ".x/review.md", gate_rounds: { final_review: [round(1, { by: "agent", reviewer: { kind: "approve", note: "" } })] } }), reviewed, 0, { kind: "chain" }, on);
    expect(v.reviewer).toMatchObject({ id: "auto_review", state: "done", chip: "approve", chipTone: "green" });
    expect(v.gate.state).toBe("done");
    expect(v.youSub).toBe("auto");
    expect(v.doc).toBeUndefined();
  });

  it("reads a gate's auto_review and message from the frozen chain, the API's chain listing no task on a gate (GR-4)", () => {
    const frozen = JSON.stringify({ chain: { nodes: [{ id: "plan", kind: "exec" }, { id: "plan_approval", kind: "gate", message: "Approve the plan.", auto_review: { id: "check", kind: "agent" } }] } });
    const s = { id: "s", hook_point: "plan_approval.auto_review", node_id: "plan_approval", status: "running", attempt: 1, round: 0, created_at: "t", started_at: "t" } as WorkerSession;
    const before = gateView(detail({ materialized_chain: frozen, current_node_id: "plan" }), V1[1], 0, { kind: "chain" }, on);
    expect(before.reviewer).toMatchObject({ id: "auto_review", state: "todo", chip: "runs first when reached" });
    expect(before.message).toBe("Approve the plan.");
    const running = gateView(detail({ materialized_chain: frozen, current_node_id: "plan_approval", worker_sessions: [s] }), V1[1], 0, { kind: "chain" }, on);
    expect(running.reviewer).toMatchObject({ id: "auto_review", state: "current" });
    expect(running.reviewer?.chip).toBeUndefined();
    // A run inside a later group (`slices.verification`) is past the gate: its reviewer is not still to come.
    const past = gateView(detail({ materialized_chain: frozen, chain_definition: { template_id: "d", nodes: SLICED }, current_node_id: "slices.verification" }), V1[1], 0, { kind: "chain" }, on);
    expect(past.reviewer?.chip).toBeUndefined();
  });

  it("an ended item whose gate was skipped has no current node: the reviewer does not claim to run first when reached", () => {
    const frozen = JSON.stringify({ chain: { nodes: [{ id: "plan", kind: "exec" }, { id: "plan_approval", kind: "gate", auto_review: { id: "check", kind: "agent" } }] } });
    const v = gateView(detail({ materialized_chain: frozen, display_status: "done", current_node_id: null }), V1[1], 0, { kind: "chain" }, on);
    expect(v.reviewer).toMatchObject({ id: "auto_review", state: "todo" });
    expect(v.reviewer?.chip).toBeUndefined();
  });

  it.each([
    ["a pass in the round reads ready for you", { kind: "ready", note: "" }, { chip: "ready for you", chipTone: "green" }],
    ["an undecided review reads could not decide", { kind: "undecided", note: "x" }, { chip: "could not decide", chipTone: "amber" }],
  ] as const)("%s", (_, reviewer, want) => {
    const frozen = JSON.stringify({ chain: { nodes: [{ id: "plan", kind: "exec" }, { id: "plan_approval", kind: "gate", auto_review: { id: "check", kind: "agent" } }] } });
    const rounds = [round(1, { outcome: "pending", by: null, ended_at: null, reviewer })];
    const v = gateView(detail({ materialized_chain: frozen, current_node_id: "plan_approval", pending_gate: "plan_approval", gate_rounds: { plan_approval: rounds } }), V1[1], 0, { kind: "chain" }, on);
    expect(v.reviewer).toMatchObject(want);
  });

  it.each([
    ["checks_first", "checks_first"],
    ["decides", "decides"],
    [undefined, "decides"],
  ] as const)("hands the canvas the reviewer's mode from the frozen chain (%s)", (mode, want) => {
    const frozen = JSON.stringify({ chain: { nodes: [{ id: "plan", kind: "exec" }, { id: "plan_approval", kind: "gate", auto_review: { id: "check", kind: "agent" }, ...(mode && { auto_review_mode: mode }) }] } });
    expect(gateView(detail({ materialized_chain: frozen, current_node_id: "plan" }), V1[1], 0, { kind: "chain" }, on).reviewer?.mode).toBe(want);
  });

  it("has no reviewer for a gate that declares none", () => {
    expect(gateView(detail(), V1[1], 0, { kind: "chain" }, on).reviewer).toBeUndefined();
  });

  it.each<[string, number | undefined, GateRound[], Record<string, unknown>]>([
    ["round 1 as it left the gate: sent back, with its note", 1, twice, { state: "failed", youSub: "changes requested · assistant", said: NOTE, taken: true, target: "plan" }],
    ["the newest, approved, has no note", undefined, twice, { state: "done", youSub: "assistant", said: undefined, taken: undefined }],
    ["a cap breach sent nothing back", 1, [round(1, { outcome: "rejected", note: "n", sent_to: null }), round(2)], { state: "failed", said: "n", taken: undefined, target: "plan" }],
    ["a fixed round repaired the worktree", 1, [round(1, { outcome: "fixed", by: "agent" }), round(2)], { state: "done", youSub: "fixed · auto", fixed: true, taken: undefined }],
    ["a stale pick shows the newest", 5, twice, { state: "done", said: undefined }],
    // Not pending and not passed: the badge says how the newest round ended, not that the gate "decides".
    ["the newest sent work back: the badge says so, with its note", undefined, [round(1, { by: "human" }), round(2, { outcome: "rejected", by: "human", note: "redo", sent_to: "plan" })], { state: "todo", youSub: "changes requested · you", said: "redo", taken: true }],
    ["the newest closed with the item", undefined, [round(1, { outcome: "closed", by: null })], { state: "todo", youSub: "closed" }],
    ["the newest approved, then the gate reopened", undefined, [round(1, { reopened: true })], { state: "todo", youSub: "approved · assistant" }],
  ])("a gate with rounds: %s", (_name, picked, rounds, want) => {
    const v = gateView(asked(...rounds), V1[1], 0, { kind: "chain" }, on, picked);
    expect(v.gate.state).toBe(want.state);
    if ("youSub" in want) expect(v.youSub).toBe(want.youSub);
    if ("said" in want) expect(v.said?.note).toBe(want.said);
    if ("taken" in want) expect(v.reject?.taken).toBe(want.taken);
    if ("fixed" in want) expect(v.reject?.fixed).toBe(want.fixed);
    if ("target" in want) expect(v.reject?.id).toBe(want.target);
    expect(v.rounds?.selected).toBe(rounds.length < 2 ? undefined : picked && picked <= 2 ? picked : 2);
  });

  it("sends a round's work back to where it went, not where the gate would reject to now", () => {
    const item = asked(round(1, { outcome: "rejected", note: "n", sent_to: "merge_request" }), round(2));
    expect(gateView(item, V1[1], 0, { kind: "chain" }, on, 1).reject).toMatchObject({ id: "merge_request", taken: true });
  });

  it("has no document on a past round, though the gate is waiting on its newest", () => {
    const item = { ...asked(round(1, { outcome: "rejected", note: "n", sent_to: "plan" }), round(2, { outcome: "pending", by: null, ended_at: null })), pending_gate: "plan_approval" };
    expect(gateView(item, V1[1], 0, { kind: "chain" }, on, 1).doc).toBeUndefined();
    expect(gateView(item, V1[1], 0, { kind: "chain" }, on).doc?.label).toBe("plan.md");
  });

  it("a past round's reviewer chip is that round's own verdict", () => {
    const frozen = JSON.stringify({ chain: { nodes: [{ id: "plan", kind: "exec" }, { id: "plan_approval", kind: "gate", auto_review: { id: "check", kind: "agent" } }] } });
    const item = { ...asked(round(1, { outcome: "rejected", note: "n", sent_to: "plan", reviewer: { kind: "ready", note: "" } }), round(2)), materialized_chain: frozen };
    expect(gateView(item, V1[1], 0, { kind: "chain" }, on, 1).reviewer?.chip).toBe("ready for you");
    expect(gateView(item, V1[1], 0, { kind: "chain" }, on).reviewer?.chip).toBeUndefined();
  });

  it("says a reopened gate is not passed", () => {
    expect(gateView(asked(round(1, { reopened: true })), V1[1], 0, { kind: "chain" }, on).gate.state).toBe("todo");
  });
});
