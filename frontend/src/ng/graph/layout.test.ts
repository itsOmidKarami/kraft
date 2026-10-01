import { describe, expect, it } from "vitest";
import { arcShapes, L, layout, seamX, type ChainNode } from "./layout";

const chain: ChainNode[] = [
  { id: "a", kind: "exec", state: "done" },
  { id: "g", kind: "gate", state: "done" },
  { id: "b", kind: "exec", state: "todo" },
];

describe("chain layout", () => {
  it("keeps the prototype's constants", () => {
    expect(L).toEqual({ BOX: 54, GATE: 26, COLE: 138, COLG: 96, CY: 96, PAD: 86, H: 250 });
  });
  it("puts exec columns 138 wide and gates 96, centred, from PAD", () => {
    const lay = layout(chain);
    expect(lay.items.map((i) => i.cx)).toEqual([86 + 69, 86 + 138 + 48, 86 + 138 + 96 + 69]);
    expect(lay.W).toBe(86 + 138 + 96 + 138 + 86);
    expect(lay.H).toBe(250);
  });
  it("stops edges 5px short of each box, diamond and mark", () => {
    const lay = layout(chain);
    // start mark (36, r 9) → a (155, half 27)
    expect(lay.edges[0]).toMatchObject({ x1: 36 + 9 + 5, x2: 155 - 27 - 5, d: "M50 96 L123 96" });
    // a → gate (272, half 26 · 0.72)
    expect(lay.edges[1].x2).toBeCloseTo(272 - 18.72 - 5);
    // b → end mark (W − 36, r 8)
    expect(lay.edges[3].x2).toBe(lay.W - 36 - 8 - 5);
  });
  it("dashes the edge into a todo node, and the end edge after one", () => {
    expect(layout(chain).edges.map((e) => e.todo)).toEqual([false, false, true, true]);
  });
  it("dots the middle of every edge but the first and last", () => {
    const lay = layout(chain);
    expect(lay.dots.map((d) => d.x)).toEqual([(lay.edges[1].x1 + lay.edges[1].x2) / 2, (lay.edges[2].x1 + lay.edges[2].x2) / 2]);
  });
  it("puts seam `at` in the middle of edge `at`", () => {
    const lay = layout(chain);
    expect(seamX(lay, 1)).toBe((lay.edges[1].x1 + lay.edges[1].x2) / 2);
    expect(seamX(lay, 9)).toBeNull();
  });
  it("draws the loop above its node", () => {
    const [loop] = arcShapes(layout(chain), [{ kind: "loop", node: "a", tone: "active", label: "fix" }]);
    // top = CY − BOX/2 = 69
    expect(loop.d).toBe("M167 66 C189 25 121 25 143 66");
    expect(loop.arrow).toBe("M137 60 L143 66 L149 59");
    expect(loop).toMatchObject({ dashed: false, label: { x: 155, y: 29, text: "fix" } });
    expect(arcShapes(layout(chain), [{ kind: "loop", node: "a" }])[0].dashed).toBe(true);
  });
  it("draws the rebase above the chain and the reject below it", () => {
    const [rebase, reject] = arcShapes(layout(chain), [{ kind: "rebase", from: "b", to: "a" }, { kind: "reject", from: "g", to: "a" }]);
    expect(rebase.d).toBe("M365 66 C365 -23 179 -23 179 66");
    expect(rebase.label).toEqual({ x: 272, y: 0, text: "rebase → a" });
    expect(reject.d).toBe("M272 116 C272 222 155 222 155 170");
    expect(reject.arrow).toBe("M150 177 L155 170 L160 177");
    expect(reject.label).toEqual({ x: 213.5, y: 196, text: "reject" });
  });
});
