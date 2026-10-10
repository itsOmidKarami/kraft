import { describe, expect, it } from "vitest";
import type { ChainNode } from "../../types";
import { placeUrl, pushes, readPlace } from "./url";

const nodes: ChainNode[] = [
  { id: "plan", gate_after: null, tasks: ["plan.write.plan"], steps: [["plan.write.plan"]] },
  { id: "verification", gate_after: null, tasks: ["verification.checks.lint", "verification.checks.typecheck"], steps: [["verification.checks.lint", "verification.checks.typecheck"]] },
];
const gated: ChainNode[] = [...nodes, { id: "final_review", kind: "gate", gate_after: null, tasks: [], steps: [] }];
const q = (s: string) => new URLSearchParams(s);

describe("item URL", () => {
  it("reads node view, selection, tab and attempt", () => {
    expect(readPlace("verification", q("sel=verification.checks.lint&tab=log&attempt=2"), nodes)).toEqual({
      node: "verification",
      sel: { kind: "task", node: "verification", step: "checks", task: "lint" },
      tab: "log",
      attempt: 2,
    });
  });

  it("reads and writes a fix loop's tasks, the task `<step>.<task>` in a loop of steps, only on a node that has a loop", () => {
    const looped: ChainNode[] = [{ ...nodes[1], fix_loop: "verification.fix_loop" }];
    expect(readPlace("verification", q("sel=verification.fix_loop.judge"), looped).sel).toEqual({ kind: "task", node: "verification", step: "fix_loop", task: "judge" });
    const deep = readPlace("verification", q("sel=verification.fix_loop.repair.fix"), looped);
    expect(deep.sel).toEqual({ kind: "task", node: "verification", step: "fix_loop", task: "repair.fix" });
    expect(placeUrl("w1", deep)).toBe("/work-items/w1/nodes/verification?sel=verification.fix_loop.repair.fix");
    expect(readPlace("verification", q("sel=verification.fix_loop.judge"), nodes).sel).toEqual({ kind: "node", node: "verification" });
  });

  it("reads and writes the document open over the page, so a shared link lands on it", () => {
    expect(readPlace(undefined, q("doc=d1"), nodes).doc).toBe("d1");
    expect(readPlace(undefined, q(""), nodes).doc).toBeUndefined();
    expect(placeUrl("w1", { sel: { kind: "chain" }, doc: "d1" })).toBe("/work-items/w1?doc=d1");
    // The search that opened it rides along, and means nothing without a document.
    expect(readPlace(undefined, q("doc=d1&q=cache+bound"), nodes)).toMatchObject({ doc: "d1", q: "cache bound" });
    expect(readPlace(undefined, q("q=cache"), nodes).q).toBeUndefined();
    expect(placeUrl("w1", { sel: { kind: "chain" }, doc: "d1", q: "cache bound" })).toBe("/work-items/w1?doc=d1&q=cache+bound");
    expect(placeUrl("w1", { sel: { kind: "chain" }, q: "cache" })).toBe("/work-items/w1");
  });

  it("knows a node's escalation task, which no step lists", () => {
    expect(readPlace("verification", q("sel=verification.escalation.escalation&tab=thread"), nodes).sel).toEqual({ kind: "task", node: "verification", step: "escalation", task: "escalation" });
  });

  it("round-trips the fix-loop round a node view shows, and reads no round that is not a count", () => {
    const sel = { kind: "task", node: "verification", step: "checks", task: "lint" } as const;
    expect(readPlace("verification", q("sel=verification.checks.lint&round=2"), nodes)).toMatchObject({ sel, round: 2 });
    expect(placeUrl("w1", { node: "verification", sel, round: 2 })).toBe("/work-items/w1/nodes/verification?sel=verification.checks.lint&round=2");
    for (const not of ["0", "1.5", "two"]) expect(readPlace("verification", q(`round=${not}`), nodes).round).toBeUndefined();
    // A round is a node view's: the chain view neither reads one nor writes one.
    expect(readPlace(undefined, q("round=2"), nodes).round).toBeUndefined();
    expect(placeUrl("w1", { sel: { kind: "chain" }, round: 2 })).toBe("/work-items/w1");
  });

  it("round-trips a gate's reviewer as <gate>.auto_review, and reads it on no other node", () => {
    const sel = { kind: "task", node: "final_review", step: "auto_review", task: "auto_review" } as const;
    expect(readPlace("final_review", q("sel=final_review.auto_review&tab=log&attempt=2"), gated)).toMatchObject({ sel, tab: "log", attempt: 2 });
    expect(placeUrl("w1", { node: "final_review", sel, tab: "log", attempt: 2 })).toBe("/work-items/w1/nodes/final_review?sel=final_review.auto_review&tab=log&attempt=2");
    expect(readPlace("verification", q("sel=verification.auto_review"), gated).sel).toEqual({ kind: "node", node: "verification" });
  });

  it("falls back to the floor for an unknown sel, and to the chain for an unknown node", () => {
    expect(readPlace("verification", q("sel=verification.nope"), nodes).sel).toEqual({ kind: "node", node: "verification" });
    expect(readPlace(undefined, q("sel=ghost"), nodes).sel).toEqual({ kind: "chain" });
    expect(readPlace("ghost", q(""), nodes)).toEqual({ node: undefined, sel: { kind: "chain" }, tab: undefined, attempt: undefined });
  });

  it("writes the same place back, leaving out what the route already says", () => {
    const place = readPlace("verification", q("sel=verification.checks&tab=config"), nodes);
    expect(placeUrl("w1", place)).toBe("/work-items/w1/nodes/verification?sel=verification.checks&tab=config");
    expect(placeUrl("w1", { node: "verification", sel: { kind: "node", node: "verification" } })).toBe("/work-items/w1/nodes/verification");
    expect(placeUrl("w1", { sel: { kind: "chain" } })).toBe("/work-items/w1");
    expect(placeUrl("w1", { sel: { kind: "node", node: "plan" }, tab: "config" })).toBe("/work-items/w1?sel=plan&tab=config");
  });

  it("pushes only when entering or leaving a node view", () => {
    const chain = { sel: { kind: "chain" as const } };
    expect(pushes(chain, { node: "plan", sel: { kind: "node", node: "plan" } })).toBe(true);
    expect(pushes({ node: "plan", sel: { kind: "node", node: "plan" } }, chain)).toBe(true);
    expect(pushes(chain, { sel: { kind: "node", node: "plan" } })).toBe(false);
  });
});
