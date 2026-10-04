import { describe, expect, it } from "vitest";
import { arcYAt, curve, G, loopArc, loopSlots, nodeEdges, nodeLayout, sideBranch, type NodeStep } from "./nodeLayout";

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
    expect(loopArc(lay).d).toContain(`C${lay.endX} ${lay.loopY} 23 ${lay.loopY}`);
    // The label follows the swoop's lowest point, and the world ends 16px under it.
    const labelY = lay.TY + 0.75 * (lay.loopY - lay.TY) + 14;
    expect(loopArc(lay, "fix").label!.y).toBe(labelY);
    expect(lay.H).toBe(labelY + 16);
  });
  it("makes an arc that carries tasks deeper by the prototype's 78px of control depth, with its label on the line", () => {
    const bare = nodeLayout([step("a", 1)], { loop: true }), full = nodeLayout([step("a", 1)], { loop: true, loopTasks: true });
    // NodeGraph.dc.html: the world grows 92 instead of 34 and the controls sit at H − 30 + 40, not + 20.
    expect(full.loopY).toBe(bare.loopY + 78);
    expect(full.loopY).toBe(bare.H - 34 + 102);
    // The curve's lowest point is 0.75 of the way down its controls (+2.4px from its ends being 9 and 10 below the axis).
    expect(loopArc(full, "fix").label!.y).toBeCloseTo(full.TY + 0.75 * (full.loopY - full.TY) + 2.375, 5);
    expect(full.H).toBeGreaterThan(loopArc(full, "fix").label!.y + 22 + 40);
  });
  it("seats the repair left of centre and the judge right of it, ±110px or a quarter of the arc's width, on the curve", () => {
    const wide = nodeLayout([step("a", 1), step("b", 1), step("c", 1)], { loop: true, loopTasks: true });
    const mid = (wide.endX + 23) / 2;
    const [repair, judge] = loopSlots(wide, 1, true);
    expect([repair.x, judge.x]).toEqual([mid - 110, mid + 110]);
    // Both are on the cubic, below the axis, and no lower than its lowest point.
    expect(repair.y).toBeGreaterThan(wide.TY + 60);
    expect(repair.y).toBeLessThanOrEqual(loopArc(wide, "x").label!.y + 1e-6);
    expect(Math.abs(repair.y - judge.y)).toBeLessThan(1);
    const narrow = nodeLayout([step("a", 1)], { loop: true, loopTasks: true });
    const [nr, nj] = loopSlots(narrow, 1, true);
    expect(nj.x - nr.x).toBe((narrow.endX - 23) / 2);
    // A repair alone sits left of centre too, where the prototype puts the first of its tasks.
    expect(loopSlots(narrow, 1, false)[0].x).toBe((narrow.endX + 23) / 2 - (narrow.endX - 23) / 4);
  });

  describe("an open task", () => {
    const steps = [step("a", 1), step("b", 1), step("c", 1)];
    const open = { step: 1, row: 0, w: 760, h: 200 };
    it("widens its column to the frame and 12px, centres the frame in it, and moves the later columns, the end mark and the world right", () => {
      const bare = nodeLayout(steps), lay = nodeLayout(steps, { expand: open });
      // Half the extra 668px moves the open column's own centre; all of it the columns after.
      expect(lay.cx).toEqual([bare.cx[0], bare.cx[1] + 334, bare.cx[2] + 668]);
      expect(lay.endX).toBe(bare.endX + 668);
      expect(lay.W).toBe(bare.W + 668);
      expect(nodeLayout(steps, { expand: { ...open, step: 2 } }).endX).toBe(bare.endX + 668);
    });
    it("draws the frame centred on its column and on the axis, and drops the axis for a tall frame", () => {
      const lay = nodeLayout(steps, { expand: open });
      expect(lay.cols[1].open).toEqual({ x: lay.cx[1] - 380, y: lay.TY - 100, w: 760, h: 200 });
      // The prototype: TY = max(TY, TOP + h/2 + 4).
      expect(lay.TY).toBe(Math.max(42 + 22 + (248 - 92) / 2, 42 + 100 + 4));
      expect(lay.cols[0].open).toBeNull();
      // The step's label centres on the column, 38px over the frame, as over a box.
      expect(lay.cols[1].labelY).toBe(lay.TY - 100 - 38);
      expect(lay.cols[0].labelY).toBe(lay.cols[0].ys[0] - 22 - 38);
      expect(lay.H).toBeGreaterThanOrEqual(lay.TY + 100 + 70);
    });
    it("runs the edges to the frame's edges and joins the next step at the columns' midpoint, as the prototype does", () => {
      const lay = nodeLayout(steps, { expand: open });
      const { edges, dots } = nodeEdges(steps, lay);
      const frameRight = lay.cx[1] + 380;
      expect(edges.find((e) => e.key === "o2_0")!.d.startsWith(`M${frameRight + 4} ${lay.TY}`)).toBe(true);
      expect(edges.find((e) => e.key === "i1_0")!.d.endsWith(`${lay.cx[1] - 380 - 4} ${lay.TY}`)).toBe(true);
      expect(dots.map((d) => d.x)).toEqual([(lay.cx[0] + lay.cx[1]) / 2, (lay.cx[1] + lay.cx[2]) / 2]);
    });
    it("keeps the arc clear of the frame's foot, and moves the loop's tasks with the columns", () => {
      const lay = nodeLayout(steps, { loop: true, loopTasks: true, expand: { ...open, h: 360 } });
      const x1 = G.startX + 9, f0 = lay.cx[1] - 380;
      for (const x of [f0, f0 + 380, f0 + 760]) expect(arcYAt(lay.TY, lay.endX, x1, lay.loopY, x)).toBeGreaterThanOrEqual(lay.TY + 180 + 28);
      const bare = nodeLayout(steps, { loop: true, loopTasks: true });
      expect(loopSlots(lay, 1, true)[0].x).toBe(loopSlots(bare, 1, true)[0].x + 334);
    });
    it("takes the frame's height in its row, siblings 48px clear of it above and below, the step's frame wrapping them", () => {
      const lay = nodeLayout([step("a", 3)], { expand: { step: 0, row: 1, w: 760, h: 300 } });
      const [y0, y1, y2] = lay.cols[0].ys;
      // The middle row is the frame: boxes 48px from its head and foot.
      expect(y1).toBe(lay.TY);
      expect(y1 - 150 - (y0 + 22)).toBe(48);
      expect(y2 - 22 - (y1 + 150)).toBe(48);
      const f = lay.cols[0].frame!;
      expect(f.w).toBe(772 + 12);
      expect(f.x).toBe(lay.cx[0] - 386 - 6);
      expect(f.y).toBe(y0 - 22 - 16);
      expect(f.y + f.h).toBe(y2 + 22 + 44);
    });
    it("is the same layout without one, or for a task the step does not have", () => {
      const bare = nodeLayout(steps, { loop: true });
      expect(nodeLayout(steps, { loop: true, expand: { step: 1, row: 4, w: 760, h: 200 } })).toEqual(bare);
    });
  });
});
