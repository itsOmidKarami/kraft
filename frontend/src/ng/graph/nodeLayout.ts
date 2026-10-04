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

/** The width of the changed-test-scope task's frame once it opens, and its column: the frame and 12px. */
export const EXPAND_W = 760;
/** One task of one step drawn open: a frame `w` wide and `h` tall in place of its box. */
export type Expand = { step: number; row: number; w: number; h: number };

const rowsOf = (st: NodeStep) => Math.max(1, st.tasks.length);

/** Node canvas layout: steps as columns, a step's tasks as rows centred on the axis TY. One task may be
 *  `expand`ed: its column then holds a frame in place of the box, the columns after it move right and its
 *  row grows, and everything that hangs off the steps (the arc, the end mark, the loop's tasks) follows. */
export function nodeLayout(steps: NodeStep[], o: { side?: boolean; loop?: boolean; loopTasks?: boolean; footer?: boolean; expand?: Expand } = {}) {
  const ex = o.expand && steps[o.expand.step]?.tasks[o.expand.row] ? o.expand : undefined;
  // The open task's column is the frame and 12px, and the frame sits at its centre.
  const colW = (k: number) => (ex && ex.step === k ? ex.w + 12 : G.COL);
  // A row is a ROW tall; the expanded one as tall as its frame and the normal 48px between boxes above and below it.
  const rowH = (k: number, j: number) => (ex && ex.step === k && ex.row === j ? Math.max(G.ROW, ex.h + (G.ROW - G.BOX)) : G.ROW);
  const block = (k: number) => Array.from({ length: rowsOf(steps[k]) }, (_, j) => rowH(k, j)).reduce((t, h) => t + h, 0);
  const maxB = Math.max(G.ROW, ...steps.map((_, k) => block(k)));
  let TY = G.TOP + G.BOX / 2 + (maxB - G.ROW) / 2;
  if (ex) TY = Math.max(TY, G.TOP + ex.h / 2 + 4);
  /** A step's row centres, and the half-height of each row's drawing (the box, or the frame). */
  const rows = (k: number) => {
    let y = TY - block(k) / 2;
    return Array.from({ length: rowsOf(steps[k]) }, (_, j) => {
      const h = rowH(k, j);
      const c = y + h / 2;
      y += h;
      return { y: c, half: ex && ex.step === k && ex.row === j ? ex.h / 2 : G.BOX / 2 };
    });
  };
  let edge = G.startX + 68;
  const cx = steps.map((_, k) => { const c = edge + colW(k) / 2; edge += colW(k) + G.GAP; return c; });
  // What a column's edges meet: the box's, or the open frame's.
  const half = (k: number) => (ex && ex.step === k ? ex.w / 2 : G.BOX / 2);
  const endX = steps.length ? cx[cx.length - 1] + colW(steps.length - 1) / 2 + 44 : G.startX + 18 + 110;
  let W = endX + 24;
  const bottom = Math.max(...steps.map((_, k) => { const r = rows(k).at(-1)!; return r.y + r.half; }), TY + G.BOX / 2);
  let H = bottom + 70;
  if (o.side) { W = Math.max(W, endX + 84 + G.COL / 2 + 10); H = Math.max(H, TY + 74 + G.BOX / 2 + 44); }
  // The loop's control depth. The prototype's (H − 10 once the loop's 34px are added)
  // never drew a parallel first step: its swoop reaches only ~0.56 of that depth at the
  // first column, so deeper rows push it down to 1.8× their bottom, labels included (R40).
  // Its label sits 14px under the swoop's lowest point, 0.75 of the control depth.
  let loopY = 0, loopLabelY = 0;
  if (o.loop) {
    const below = bottom - TY + 40;
    const proto = H + 34 - 10;
    loopY = Math.max(proto, TY + Math.ceil(1.8 * below));
    loopLabelY = TY + 0.75 * (loopY - TY) + 14;
    H = Math.max(proto + 10, loopLabelY + 16);
    if (o.loopTasks) {
      // An arc that carries the repair and the judge is 92px deeper; its label sits on the line,
      // between the two, and the world ends under their boxes and labels.
      loopY += LOOP_TASKS_EXTRA;
    }
    if (ex) {
      // And under an open frame it clears the frame's foot by 28px along the whole of it.
      const x1 = G.startX + 9, fx0 = cx[ex.step] - ex.w / 2, fx1 = fx0 + ex.w, foot = TY + ex.h / 2 + 28;
      for (let i = 0; i < 400 && [fx0, (fx0 + fx1) / 2, fx1].some((x) => arcYAt(TY, endX, x1, loopY, x) < foot); i++) loopY += 8;
      if (!o.loopTasks) {
        loopLabelY = TY + 0.75 * (loopY - TY) + 14;
        H = Math.max(proto + 10, loopLabelY + 16);
      }
    }
    if (o.loopTasks) {
      const low = arcLow(TY, loopY);
      loopLabelY = low;
      H = Math.max(proto + 10, low + G.BOX / 2 + 52);
    }
  }
  const footerY = H + 4;
  if (o.footer) H += 26;
  const cols = steps.map((st, k) => {
    const r = rows(k), ys = r.map((x) => x.y);
    const top = r[0].y - r[0].half, foot = r.at(-1)!.y + r.at(-1)!.half;
    const opened = ex && ex.step === k ? ex : undefined;
    // The soft box only around a parallel step (Decisions §7); it wraps the open frame and its siblings, 16px clear of the frame.
    const frame = st.tasks.length >= 2 ? { x: cx[k] - colW(k) / 2 - 6, y: top - 16, w: colW(k) + 12, h: foot + (opened && opened.row === st.tasks.length - 1 ? 16 : 44) - (top - 16) } : null;
    // The label centres on the column, 38px over the first row's box or the open frame.
    return { cx: cx[k], ys, frame, labelY: top - 38, left: cx[k] - half(k), right: cx[k] + half(k), open: opened ? { x: cx[k] - opened.w / 2, y: ys[opened.row] - opened.h / 2, w: opened.w, h: opened.h } : null };
  });
  return { TY, cx, endX, W, H, cols, maxRows: Math.max(1, ...steps.map(rowsOf)), footerY, loopY, loopLabelY };
}
export type NodeLayout = ReturnType<typeof nodeLayout>;

type Edge = { key: string; d: string; todo: boolean };

/** Every task row's own curve in and out, through a join dot between steps. */
export function nodeEdges(steps: NodeStep[], lay: NodeLayout) {
  const { TY, endX, cols } = lay;
  const edges: Edge[] = [];
  const dots: { x: number; y: number }[] = [];
  const todo = (t?: GraphItem) => t?.state === "todo";
  const inX = (k: number) => cols[k].left - 4, outX = (k: number) => cols[k].right + 4;
  steps.forEach((st, k) => {
    const { ys } = cols[k];
    if (k === 0) ys.forEach((y, i) => edges.push({ key: `in${i}`, d: curve(G.startX + 18, TY, inX(0), y), todo: todo(st.tasks[i]) }));
    else {
      // Halfway between the columns' centres, as the prototype joins them: behind an open frame when one is next to it.
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
