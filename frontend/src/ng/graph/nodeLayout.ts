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

/** What an arc's control depth grows by when it carries tasks, px: the prototype's 92px of world height
 *  (against 34 for the bare arc) less the 14 its control points already sit under the world's foot. */
export const LOOP_TASKS_EXTRA = 78;
/** The arc's lowest point: a cubic with both controls at `y` is at 0.75 of the way down there. */
const arcLow = (TY: number, y: number) => (6 * y + 2 * TY + 19) / 8;

/** The y of the fix-loop arc under world x: it runs from the end mark (x = `e`) back to the start (x = `x1`),
 *  a cubic whose controls sit `y` deep, x monotone in t, so bisect. */
export function arcYAt(TY: number, e: number, x1: number, y: number, px: number) {
  const x = (t: number) => e * (1 - t) ** 2 * (1 + 2 * t) + x1 * t * t * (3 - 2 * t);
  let lo = 0, hi = 1;
  for (let i = 0; i < 30; i++) { const m = (lo + hi) / 2; if (x(m) > px) lo = m; else hi = m; }
  const t = (lo + hi) / 2, u = 1 - t;
  return u ** 3 * (TY + 9) + 3 * u * u * t * y + 3 * u * t * t * y + t ** 3 * (TY + 10);
}

const rowsOf = (st: NodeStep) => Math.max(1, st.tasks.length);

/** Steps as columns; a step's tasks as rows centred on the axis TY. */
export function nodeLayout(steps: NodeStep[], o: { side?: boolean; loop?: boolean; loopTasks?: boolean; footer?: boolean } = {}) {
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
  // Its label sits 14px under the swoop's lowest point, 0.75 of the control depth.
  let loopY = 0, loopLabelY = 0;
  if (o.loop) {
    const bottom = ((maxRows - 1) / 2) * G.ROW + G.BOX / 2 + 40;
    const proto = H + 34 - 10;
    loopY = Math.max(proto, TY + Math.ceil(1.8 * bottom));
    loopLabelY = TY + 0.75 * (loopY - TY) + 14;
    H = Math.max(proto + 10, loopLabelY + 16);
    if (o.loopTasks) {
      // An arc that carries the repair and the judge is deeper; its label sits on the line,
      // between the two, and the world ends under their boxes and labels.
      loopY += LOOP_TASKS_EXTRA;
      const low = arcLow(TY, loopY);
      loopLabelY = low;
      H = Math.max(proto + 10, low + G.BOX / 2 + 52);
    }
  }
  const footerY = H + 4;
  if (o.footer) H += 26;
  const cols = steps.map((st, k) => {
    const y = ys(rowsOf(st));
    // The soft box only around a parallel step (Decisions §7).
    const frame = st.tasks.length >= 2 ? { x: cx[k] - G.COL / 2 - 6, y: y[0] - G.BOX / 2 - 16, w: G.COL + 12, h: (y.length - 1) * G.ROW + G.BOX + 60 } : null;
    return { cx: cx[k], ys: y, frame, labelY: y[0] - G.BOX / 2 - 38 };
  });
  return { TY, cx, endX, W, H, cols, maxRows, footerY, loopY, loopLabelY };
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
    label: label ? { x: (e + x1) / 2, y: lay.loopLabelY, text: label } : undefined,
  };
}

/** Where the fix loop's tasks sit on its arc, left to right, as `loopArc` draws it:
 *  repair tasks, then the judge, which decides first (the arc runs right to left).
 *  The pair sits ±110px of centre, or a quarter of the arc's width when that is less. */
export function loopSlots(lay: NodeLayout, repairs: number, judge: boolean) {
  const x1 = G.startX + 9, e = lay.endX, mid = (e + x1) / 2;
  const off = Math.min(110, (e - x1) / 4);
  const xs = [
    ...Array.from({ length: repairs }, (_, i) => mid - off - (repairs - 1 - i) * Math.max(2 * off, G.COL + 10)),
    ...(judge ? [mid + off] : []),
  ];
  return xs.map((px) => ({ x: px, y: arcYAt(lay.TY, e, x1, lay.loopY, px) }));
}
