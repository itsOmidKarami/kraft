import { describe, expect, it } from "vitest";
import { curve, G, loopArc, nodeEdges, nodeLayout, sideBranch, type NodeStep } from "./nodeLayout";

const step = (id: string, n: number): NodeStep => ({ id, tasks: Array.from({ length: n }, (_, i) => ({ id: `${id}${i}` })) });

describe("node layout", () => {
  it("keeps the prototype's constants", () => {
    expect(G).toEqual({ BOX: 44, COL: 104, GAP: 50, ROW: 92, TOP: 42, startX: 14 });
  });
  it("puts parallel tasks in rows 92px apart, centred on the axis", () => {
    const lay = nodeLayout([step("a", 3), step("b", 2), step("c", 1)]);
    // TY = TOP + (3 − 1)/2 · ROW + BOX/2
    expect(lay.TY).toBe(42 + 92 + 22);
    expect(lay.cols[0].ys).toEqual([64, 156, 248]);
    expect(lay.cols[1].ys).toEqual([110, 202]);
    expect(lay.cols[2].ys).toEqual([156]);
  });
  it("spaces steps COL + GAP apart from startX + 68", () => {
    expect(nodeLayout([step("a", 1), step("b", 1)]).cx).toEqual([14 + 68 + 52, 14 + 68 + 52 + 154]);
  });
  it("ends 44px past the last column, and sizes the world", () => {
    const lay = nodeLayout([step("a", 2)]);
    expect(lay.endX).toBe(134 + 52 + 44);
    expect(lay.W).toBe(230 + 24);
    expect(lay.H).toBe(lay.TY + 46 + 22 + 70);
    expect(nodeLayout([step("a", 1)], { loop: true }).H).toBe(nodeLayout([step("a", 1)]).H + 34);
    expect(nodeLayout([step("a", 2)], { side: true }).W).toBe(230 + 84 + 52 + 10);
  });
  it("frames a step only from two tasks", () => {
    const lay = nodeLayout([step("a", 2), step("b", 1)]);
    expect(lay.cols[0].frame).toEqual({ x: 134 - 52 - 6, y: lay.cols[0].ys[0] - 22 - 16, w: 116, h: 92 + 44 + 60 });
    expect(lay.cols[1].frame).toBeNull();
  });
  it("curves with control points at 0.55 of the run", () => {
    const n = curve(0, 0, 100, 50).match(/-?[\d.]+/g)!.map(Number);
    expect(n.map((x) => Math.round(x * 1e6) / 1e6)).toEqual([0, 0, 55, 0, 45, 50, 100, 50]);
  });
  it("gives each row its own edge in and out, through a join dot", () => {
    const steps = [step("a", 2), { id: "b", tasks: [{ id: "b0", state: "todo" as const }] }];
    const lay = nodeLayout(steps);
    const { edges, dots } = nodeEdges(steps, lay);
    expect(edges.map((e) => e.key)).toEqual(["in0", "in1", "o1_0", "o1_1", "i1_0", "out0"]);
    expect(dots).toEqual([{ x: (lay.cx[0] + lay.cx[1]) / 2, y: lay.TY }]);
    expect(edges.find((e) => e.key === "i1_0")!.todo).toBe(true);
  });
  it("hangs the escalation branch off the end", () => {
    const lay = nodeLayout([step("a", 1)], { side: true });
    expect(sideBranch(lay)).toMatchObject({ x: lay.endX + 84, y: lay.TY + 74 });
  });
  it("draws a one-row node's fix loop exactly as the prototype does", () => {
    const lay = nodeLayout([step("a", 1)], { loop: true });
    // The prototype: by = H − 30 with H = TY + BOX/2 + 70 + 34; control points at by + 20.
    const H = lay.TY + 22 + 70 + 34, by = H - 30;
    expect(lay.H).toBe(H);
    expect(loopArc(lay, "fix").d).toBe(`M${lay.endX} ${lay.TY + 9} C${lay.endX} ${by + 20} 23 ${by + 20} 23 ${lay.TY + 10}`);
    expect(loopArc(lay, "fix").label).toEqual({ x: (lay.endX + 23) / 2, y: by + 5, text: "fix" });
  });
  it("drops the loop under a parallel first step to 1.8× its rows' bottom (R40)", () => {
    const lay = nodeLayout([step("a", 3), step("b", 1)], { loop: true });
    // Bottom row at TY + 92, its box half 22, label and meta 40: 154 below the axis.
    expect(lay.loopY).toBe(lay.TY + Math.ceil(1.8 * 154));
    expect(lay.H).toBe(lay.loopY + 10);
    expect(loopArc(lay).d).toContain(`C${lay.endX} ${lay.loopY} 23 ${lay.loopY}`);
  });
});
