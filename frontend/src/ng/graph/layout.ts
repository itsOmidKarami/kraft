import type { GraphItem } from "./types";

/** Chain canvas geometry, from StageGraph.dc.html `static L`. */
export const L = { BOX: 54, GATE: 26, COLE: 138, COLG: 96, CY: 96, PAD: 86, H: 250 } as const;

export type ChainNode = GraphItem & {
  kind: "exec" | "gate";
  sub?: string;
  subTone?: "amber" | "muted";
  /** What a node the run stands on waits for, in words ("waiting on CI"), for a pane or a row; the canvas does not draw it. */
  wait?: string;
};
export type ChainArc =
  | { kind: "loop"; node: string; tone?: "idle" | "active" | "red"; label?: string }
  | { kind: "rebase"; from: string; to: string; label?: string }
  | { kind: "reject"; from: string; to: string };
export type Seam = { at: number; open?: boolean; always?: boolean; title?: string };

type Pt = { cx: number; h: number; node?: ChainNode };

/** Columns left to right: exec 138 wide, gate 96, each node at its column's
 *  centre; edges between the start mark, the nodes and the end mark. */
export function layout(nodes: ChainNode[]) {
  let x = L.PAD;
  const items = nodes.map((node) => {
    const w = node.kind === "gate" ? L.COLG : L.COLE;
    const cx = x + w / 2;
    x += w;
    return { node, w, cx };
  });
  const W = x + L.PAD;
  // Half-widths the edges stop short of: a box's half, a diamond's half-diagonal, the marks' radii.
  const half = (n: ChainNode) => (n.kind === "gate" ? L.GATE * 0.72 : L.BOX / 2);
  const pts: Pt[] = [{ cx: 36, h: 9 }, ...items.map((i) => ({ cx: i.cx, h: half(i.node), node: i.node })), { cx: W - 36, h: 8 }];
  const edges = pts.slice(1).map((b, i) => {
    const a = pts[i];
    const x1 = a.cx + a.h + 5, x2 = b.cx - b.h - 5;
    const todo = b.node ? b.node.state === "todo" : a.node?.state === "todo";
    return { x1, x2, todo: !!todo, d: `M${x1} ${L.CY} L${x2} ${L.CY}` };
  });
  // A midpoint dot on every edge but the first and the last.
  const dots = edges.slice(1, -1).map((e) => ({ x: (e.x1 + e.x2) / 2, y: L.CY, todo: e.todo }));
  return { items, W, H: L.H, edges, dots };
}
export type ChainLayout = ReturnType<typeof layout>;

/** A seam sits at the middle of edge `at` (between point `at` and `at + 1`, the start mark being point 0). */
export const seamX = (lay: ChainLayout, at: number) => (lay.edges[at] ? (lay.edges[at].x1 + lay.edges[at].x2) / 2 : null);

export type ArcShape = { key: string; d: string; arrow: string; tone: "idle" | "active" | "red" | "reject"; dashed: boolean; label?: { x: number; y: number; text: string } };

/** The three arc kinds: a loop above a node, a rebase above the chain, a reject below it. */
export function arcShapes(lay: ChainLayout, arcs: ChainArc[]): ArcShape[] {
  const pos = new Map(lay.items.map((i) => [i.node.id, i.cx]));
  const top = L.CY - L.BOX / 2;
  return arcs.flatMap((a, k): ArcShape[] => {
    if (a.kind === "loop") {
      const cx = pos.get(a.node);
      if (cx == null) return [];
      const tone = a.tone ?? "idle";
      return [{ key: `loop${k}`, tone, dashed: tone !== "active", d: `M${cx + 12} ${top - 3} C${cx + 34} ${top - 44} ${cx - 34} ${top - 44} ${cx - 12} ${top - 3}`, arrow: `M${cx - 18} ${top - 9} L${cx - 12} ${top - 3} L${cx - 6} ${top - 10}`, label: a.label ? { x: cx, y: top - 40, text: a.label } : undefined }];
    }
    const f = pos.get(a.from), t = pos.get(a.to);
    if (f == null || t == null) return [];
    if (a.kind === "rebase") {
      const fx = f - 24, tx = t + 24;
      return [{ key: `rebase${k}`, tone: "idle", dashed: true, d: `M${fx} ${top - 3} C${fx} ${top - 92} ${tx} ${top - 92} ${tx} ${top - 3}`, arrow: `M${tx - 5} ${top - 10} L${tx} ${top - 3} L${tx + 5} ${top - 10}`, label: { x: (fx + tx) / 2, y: top - 69, text: a.label ?? `rebase → ${a.to}` } }];
    }
    const yb = L.CY + 126;
    return [{ key: `reject${k}`, tone: "reject", dashed: true, d: `M${f} ${L.CY + 20} C${f} ${yb} ${t} ${yb} ${t} ${L.CY + 74}`, arrow: `M${t - 5} ${L.CY + 81} L${t} ${L.CY + 74} L${t + 5} ${L.CY + 81}`, label: { x: (f + t) / 2, y: L.CY + 100, text: "reject" } }];
  });
}
