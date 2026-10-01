import type { GraphItem } from "./types";

/** Node canvas geometry, from NodeGraph.dc.html `static G`: parallel tasks are rows 92px apart. */
export const G = { BOX: 44, COL: 104, GAP: 50, ROW: 92, TOP: 42, startX: 14 } as const;

export type NodeStep = {
  id: string;
  label?: string;
  tasks: GraphItem[];
  mark?: "add" | "change";
  prob?: boolean;
  /** An empty step's "add a task" slot. */
  slot?: { label?: string };
  seamBelow?: boolean;
  seamBefore?: boolean;
};

/** A curved edge, control points at 0.55 of the run. */
export function curve(x1: number, y1: number, x2: number, y2: number) {
  const dx = (x2 - x1) * 0.55;
  return `M${x1} ${y1} C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`;
}

const rowsOf = (st: NodeStep) => Math.max(1, st.tasks.length);

/** Steps as columns; a step's tasks as rows centred on the axis TY. */
export function nodeLayout(steps: NodeStep[], o: { side?: boolean; loop?: boolean; footer?: boolean } = {}) {
  const maxRows = Math.max(1, ...steps.map(rowsOf));
  const TY = G.TOP + ((maxRows - 1) / 2) * G.ROW + G.BOX / 2;
  const ys = (n: number) => Array.from({ length: n }, (_, i) => TY + (i - (n - 1) / 2) * G.ROW);
  const cx = steps.map((_, k) => G.startX + 68 + G.COL / 2 + k * (G.COL + G.GAP));
  const endX = steps.length ? cx[cx.length - 1] + G.COL / 2 + 44 : G.startX + 18 + 110;
  let W = endX + 24;
  let H = TY + ((maxRows - 1) / 2) * G.ROW + G.BOX / 2 + 70;
  if (o.side) { W = Math.max(W, endX + 84 + G.COL / 2 + 10); H = Math.max(H, TY + 74 + G.BOX / 2 + 44); }
  // The loop's control depth. The prototype's (H − 10 once the loop's 34px are added)
  // never drew a parallel first step: its swoop reaches only ~0.56 of that depth at the
  // first column, so deeper rows push it down to 1.8× their bottom, labels included (R40).
  let loopY = 0;
  if (o.loop) {
    const bottom = ((maxRows - 1) / 2) * G.ROW + G.BOX / 2 + 40;
    loopY = Math.max(H + 34 - 10, TY + Math.ceil(1.8 * bottom));
    H = loopY + 10;
  }
  const footerY = H + 4;
  if (o.footer) H += 26;
  const cols = steps.map((st, k) => {
    const y = ys(rowsOf(st));
    // The soft box only around a parallel step (Decisions §7).
    const frame = st.tasks.length >= 2 ? { x: cx[k] - G.COL / 2 - 6, y: y[0] - G.BOX / 2 - 16, w: G.COL + 12, h: (y.length - 1) * G.ROW + G.BOX + 60 } : null;
    return { cx: cx[k], ys: y, frame, labelY: y[0] - G.BOX / 2 - 38 };
  });
  return { TY, cx, endX, W, H, cols, maxRows, footerY, loopY };
}
export type NodeLayout = ReturnType<typeof nodeLayout>;

type Edge = { key: string; d: string; todo: boolean };

/** Every task row's own curve in and out, through a join dot between steps. */
export function nodeEdges(steps: NodeStep[], lay: NodeLayout) {
  const { TY, endX, cols } = lay;
  const edges: Edge[] = [];
  const dots: { x: number; y: number }[] = [];
  const todo = (t?: GraphItem) => t?.state === "todo";
  const inX = (k: number) => cols[k].cx - G.BOX / 2 - 4, outX = (k: number) => cols[k].cx + G.BOX / 2 + 4;
  steps.forEach((st, k) => {
    const { ys } = cols[k];
    if (k === 0) ys.forEach((y, i) => edges.push({ key: `in${i}`, d: curve(G.startX + 18, TY, inX(0), y), todo: todo(st.tasks[i]) }));
    else {
      const J = (cols[k - 1].cx + cols[k].cx) / 2, prev = steps[k - 1];
      cols[k - 1].ys.forEach((y, i) => edges.push({ key: `o${k}_${i}`, d: curve(outX(k - 1), y, J, TY), todo: todo(prev.tasks[i]) }));
      ys.forEach((y, i) => edges.push({ key: `i${k}_${i}`, d: curve(J, TY, inX(k), y), todo: todo(st.tasks[i]) }));
      dots.push({ x: J, y: TY });
    }
    if (k === steps.length - 1) ys.forEach((y, i) => edges.push({ key: `out${i}`, d: curve(outX(k), y, endX - 9, TY), todo: todo(st.tasks[i]) }));
  });
  if (!steps.length) edges.push({ key: "empty", d: `M${G.startX + 18} ${TY} L${endX - 9} ${TY}`, todo: true });
  return { edges, dots };
}

/** The escalation branch: a dashed curve from the end mark down-right to its task. */
export function sideBranch(lay: NodeLayout) {
  const x = lay.endX + 84, y = lay.TY + 74;
  return { x, y, d: curve(lay.endX + 9, lay.TY, x - G.BOX / 2 - 4, y) };
}

/** The fix loop under the steps, from the end mark back to the start. */
export function loopArc(lay: NodeLayout, label?: string) {
  const y = lay.loopY, x1 = G.startX + 9, e = lay.endX;
  return {
    d: `M${e} ${lay.TY + 9} C${e} ${y} ${x1} ${y} ${x1} ${lay.TY + 10}`,
    arrow: `M${x1 - 5} ${lay.TY + 17} L${x1} ${lay.TY + 10} L${x1 + 5} ${lay.TY + 17}`,
    label: label ? { x: (e + x1) / 2, y: y - 15, text: label } : undefined,
  };
}
