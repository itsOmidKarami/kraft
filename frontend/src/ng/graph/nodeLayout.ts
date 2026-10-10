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
/** How far from the arc's ends its tasks keep (nearer, the line is too steep to sit a box on), and the least
 *  between two of them when it carries more than a pair: a column and the 10px its labels need. */
const LOOP_END = 62, LOOP_GAP = G.COL + 10;
/** An open loop step's frame: a head over its tasks' rows, as the changed-test-scope task's frame has. */
export const LOOP_FRAME = { w: 184, head: 40 } as const;
/** The corner radius of the lane a loop with a parallel step is drawn as (`loopArc`), and the least width of one
 *  that carries a stop or a pair: they sit a quarter in from its ends, level with the label at its middle, which
 *  needs its own half (some 35px) and 9 clear of a box. */
const LANE_R = 28, LANE_MIN = 4 * (G.BOX / 2 + 35 + 9);
/** Both, while a loop step is open: its frame is wider than a box and keeps clear of the lane's corner, and the loop's label (some 70px) still fits beside it. */
const loopRoom = (open: number) => (open ? { end: LOOP_FRAME.w / 2 + LANE_R + 8, gap: LOOP_FRAME.w / 2 + G.BOX / 2 + 70 } : { end: LOOP_END, gap: LOOP_GAP });

/** A fix loop's tasks as what the arc carries, in order. Their ids are `<step>.<task>`, and one step's tasks run together,
 *  so they are one stop on it (`step` names it); the judge, which has no step, is its own. */
export function loopCols<T extends { id: string }>(tasks: T[]): { step?: string; tasks: T[] }[] {
  const cols: { step?: string; tasks: T[] }[] = [];
  for (const t of tasks) {
    const step = t.id.includes(".") ? t.id.slice(0, t.id.indexOf(".")) : undefined;
    const last = cols.at(-1);
    if (step !== undefined && last?.step === step) last.tasks.push(t);
    else cols.push({ step, tasks: [t] });
  }
  return cols;
}
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

/** One task of one step drawn open: a frame `w` wide and `h` tall in place of its box. */
export type Expand = { step: number; row: number; w: number; h: number };

const rowsOf = (st: NodeStep) => Math.max(1, st.tasks.length);

/** Node canvas layout: steps as columns, a step's tasks as rows centred on the axis TY. One task may be
 *  `expand`ed: its column then holds a frame in place of the box, the columns after it move right and its
 *  row grows, and everything that hangs off the steps (the arc, the end mark, the loop's tasks) follows.
 *  `loopTasks` is how many stops the loop carries (a task, or a step of several) and `loopOpen` the tasks of the step
 *  drawn open, if one is: more than a pair of stops, or an open one, moves the end mark out until they fit, and an
 *  open step takes the loop deeper by its frame. `loopLane`: the loop has a step of several tasks, so it is drawn as
 *  a lane and not an arc (`loopArc`). */
export function nodeLayout(steps: NodeStep[], o: { side?: boolean; loop?: boolean; loopTasks?: number; loopLane?: boolean; loopOpen?: number; footer?: boolean; expand?: Expand } = {}) {
  const loopN = o.loop ? (o.loopTasks ?? 0) : 0, loopOpen = loopN ? (o.loopOpen ?? 0) : 0, loopLane = loopN > 0 && (!!o.loopLane || loopOpen > 0);
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
  let endX = steps.length ? cx[cx.length - 1] + colW(steps.length - 1) / 2 + 44 : G.startX + 18 + 110;
  // A pair sits a quarter of the arc from its ends (`loopSlots`), so an open one of the pair needs four times its room there.
  const room = loopRoom(loopOpen);
  const arcW = loopN > 2 ? (loopN - 1) * room.gap + 2 * room.end : loopOpen ? 4 * room.end : loopLane ? LANE_MIN : 0;
  endX = Math.max(endX, G.startX + 9 + arcW);
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
      // between the two, and the world ends under their boxes and labels. An open step's rows
      // centre on the loop's line under its frame's head: the loop goes as much deeper as keeps
      // the frame's top no higher than a lone task's box (the arc drops 0.58 of the depth at the least).
      loopY += LOOP_TASKS_EXTRA + (loopOpen ? (loopOpen - 1) * G.ROW + 2 * LOOP_FRAME.head : 0);
    }
    if (ex) {
      // And under an open frame it clears the frame's foot by 28px along the whole of it.
      const x1 = G.startX + 9, fx0 = cx[ex.step] - ex.w / 2, fx1 = fx0 + ex.w, foot = TY + ex.h / 2 + 28 + (loopOpen ? ((loopOpen - 1) * G.ROW) / 2 + G.BOX / 2 + LOOP_FRAME.head : 0);
      for (let i = 0; i < 400 && [fx0, (fx0 + fx1) / 2, fx1].some((x) => arcYAt(TY, endX, x1, loopY, x) < foot); i++) loopY += 8;
      if (!o.loopTasks) {
        loopLabelY = TY + 0.75 * (loopY - TY) + 14;
        H = Math.max(proto + 10, loopLabelY + 16);
      }
    }
    if (o.loopTasks) {
      const low = arcLow(TY, loopY);
      loopLabelY = low;
      H = Math.max(proto + 10, low + G.BOX / 2 + 52 + (loopOpen ? ((loopOpen - 1) * G.ROW) / 2 + 8 : 0));
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
  return { TY, cx, endX, W, H, cols, maxRows: Math.max(1, ...steps.map(rowsOf)), footerY, loopY, loopLabelY, loopOpen, loopLane };
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

/** The fix loop under the steps, from the end mark back to the start: an arc. A loop with a step of several tasks
 *  is a lane instead, straight down from the end mark, level along the arc's lowest point and straight up into the
 *  start: every stop sits on a level line, and the step, open, forks off it as a parallel step does off the axis. */
export function loopArc(lay: NodeLayout, label?: string, labelX?: number) {
  const y = lay.loopY, x1 = G.startX + 9, e = lay.endX, low = lay.loopLabelY, r = LANE_R;
  return {
    d: lay.loopLane ? `M${e} ${lay.TY + 9} V${low - r} Q${e} ${low} ${e - r} ${low} H${x1 + r} Q${x1} ${low} ${x1} ${low - r} V${lay.TY + 10}` : `M${e} ${lay.TY + 9} C${e} ${y} ${x1} ${y} ${x1} ${lay.TY + 10}`,
    arrow: `M${x1 - 5} ${lay.TY + 17} L${x1} ${lay.TY + 10} L${x1 + 5} ${lay.TY + 17}`,
    // At the loop's middle, or on the line at `labelX` when a task sits there.
    label: label ? { x: labelX ?? (e + x1) / 2, y: labelX === undefined || lay.loopLane ? low : arcYAt(lay.TY, e, x1, y, labelX), text: label } : undefined,
  };
}

/** Where the fix loop's stops sit on its arc, left to right, as `loopArc` draws it:
 *  repair steps, then the judge, which decides first (the arc runs right to left).
 *  The pair sits ±110px of centre, or a quarter of the arc's width when that is less;
 *  more than a pair is spread evenly about the centre, no wider than the arc (`nodeLayout` made it wide enough). */
export function loopSlots(lay: NodeLayout, repairs: number, judge: boolean) {
  const x1 = G.startX + 9, e = lay.endX, mid = (e + x1) / 2;
  const off = Math.min(110, (e - x1) / 4);
  const n = repairs + (judge ? 1 : 0);
  const gap = Math.min(2 * off, (e - x1 - 2 * loopRoom(lay.loopOpen).end) / (n - 1));
  // One sits where the pair's own half would: a repair left of centre, the judge right of it.
  const xs = n > 2 ? Array.from({ length: n }, (_, i) => mid + (i - (n - 1) / 2) * gap) : n === 2 ? [mid - off, mid + off] : n === 1 ? [judge ? mid + off : mid - off] : [];
  // On the arc, or on the lane's level line.
  return xs.map((px) => ({ x: px, y: lay.loopLane ? lay.loopLabelY : arcYAt(lay.TY, e, x1, lay.loopY, px) }));
}

/** A loop step of `n` tasks open at its slot: their rows, centred on the lane as a parallel step's are on the axis,
 *  and the frame around them, its head 4px over the first box and 50 under the last for its label and meta. */
export function loopColumn(slot: { x: number; y: number }, n: number) {
  const ys = Array.from({ length: n }, (_, j) => slot.y + (j - (n - 1) / 2) * G.ROW);
  return { ys, frame: { x: slot.x - LOOP_FRAME.w / 2, y: ys[0] - G.BOX / 2 - 4 - LOOP_FRAME.head, w: LOOP_FRAME.w, h: LOOP_FRAME.head + 4 + (n - 1) * G.ROW + G.BOX + 50 } };
}

/** An open loop step's tasks as a fork off the lane, as a parallel step's are off the axis. The loop runs right to
 *  left: it splits at a join on the frame's right edge, a branch to each task, and joins again on the left edge;
 *  between the joins (`cut`) the lane itself is not drawn. */
export function loopFork(slot: { x: number; y: number }, column: ReturnType<typeof loopColumn>) {
  const f = column.frame, from = { x: f.x + f.w, y: slot.y }, to = { x: f.x, y: slot.y };
  return {
    dots: [from, to],
    cut: [to.x, from.x],
    edges: column.ys.flatMap((y, j) => [
      { key: `lin${j}`, d: curve(from.x, from.y, slot.x + G.BOX / 2 + 4, y) },
      { key: `lout${j}`, d: curve(slot.x - G.BOX / 2 - 4, y, to.x, to.y) },
    ]),
  };
}
