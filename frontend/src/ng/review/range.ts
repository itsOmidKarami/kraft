/** A line comment's range, and which of a file's diff lines it covers. Pure. */
import type { PatchFile, PatchLine } from "./patch";
import type { Anchor, Side } from "./rows";

/** Lines `start` to `end`. Both are on `side`, unless `startSide` says the
 *  first one is on the other side: a range across sides, such as a removed
 *  line through the line that replaced it. */
export interface LineRange {
  side: Side;
  start: number;
  end: number;
  startSide?: Side;
}

export const startSideOf = (r: LineRange): Side => r.startSide ?? r.side;
export const isMixed = (r: LineRange) => startSideOf(r) !== r.side;
/** One line, on one side. */
export const isOneLine = (r: LineRange) => !isMixed(r) && r.start === r.end;

/** A file's diff lines in the unified order, and where each side's line numbers sit in it. */
export interface LineIndex {
  lines: PatchLine[];
  /** `old5` / `new5` → the line's place in `lines`; a context line is under both. */
  at: Map<string, number>;
  /** Each line's hunk, by place. */
  hunk: number[];
}

const key = (a: Anchor) => `${a.side}${a.line}`;

export function lineIndex(pf: PatchFile | undefined): LineIndex {
  const lines: PatchLine[] = [];
  const at = new Map<string, number>();
  const hunk: number[] = [];
  pf?.hunks.forEach((h, i) =>
    h.lines.forEach((l) => {
      if (l.old !== null) at.set(`old${l.old}`, lines.length);
      if (l.new !== null) at.set(`new${l.new}`, lines.length);
      hunk.push(i);
      lines.push(l);
    }),
  );
  return { lines, at, hunk };
}

export const placeOf = (ix: LineIndex, a: Anchor) => ix.at.get(key(a));

/** A one-side range holds that side's lines from `start` to `end`. A range
 *  across sides holds every line drawn between its two ends. */
export function inRange(r: LineRange, a: Anchor, ix: LineIndex): boolean {
  if (!isMixed(r)) return a.side === r.side && a.line >= r.start && a.line <= r.end;
  const p = placeOf(ix, a);
  const lo = placeOf(ix, { side: startSideOf(r), line: r.start });
  const hi = placeOf(ix, { side: r.side, line: r.end });
  return p !== undefined && lo !== undefined && hi !== undefined && p >= lo && p <= hi;
}

/** The range from `from` to `to`, whichever comes first in the diff. */
export function rangeBetween(from: Anchor, to: Anchor, ix: LineIndex): LineRange {
  if (from.side === to.side) return { side: to.side, start: Math.min(from.line, to.line), end: Math.max(from.line, to.line) };
  const [a, b] = (placeOf(ix, from) ?? 0) <= (placeOf(ix, to) ?? 0) ? [from, to] : [to, from];
  return { startSide: a.side, start: a.line, side: b.side, end: b.line };
}

/** Where `to` sits when a range that started at `from` is stretched to it.
 *  A context line is on both sides: it is read on `from`'s side, unless the
 *  lines between hold an added one, which only a range ending on the new side
 *  would keep. A line on one side only is that line, whichever side it is on. */
export function toward(to: Anchor, from: Anchor, ix: LineIndex): Anchor {
  const p = placeOf(ix, to);
  if (p === undefined || ix.lines[p].kind !== " ") return to;
  const l = ix.lines[p];
  const q = placeOf(ix, from) ?? p;
  const [lo, hi] = q < p ? [q, p] : [p, q];
  const added = from.side === "old" && ix.lines.slice(lo + 1, hi).some((x) => x.kind === "+");
  const side = added ? "new" : from.side;
  return { side, line: side === "old" ? l.old! : l.new! };
}

/** The lines a range covers, each led by its diff mark, for quoting it: a
 *  one-side range's lines on that side, a range across sides every line
 *  between its ends. `…` stands for lines between hunks the diff doesn't show. */
export function quoteOf(r: LineRange, ix: LineIndex): string[] {
  const out: string[] = [];
  let last = -1;
  ix.lines.forEach((l, i) => {
    const on = (side: Side) => (side === "old" ? l.old : l.new) !== null;
    const hit = isMixed(r)
      ? inRange(r, { side: l.new !== null ? "new" : "old", line: (l.new ?? l.old)! }, ix)
      : on(r.side) && inRange(r, { side: r.side, line: (r.side === "old" ? l.old : l.new)! }, ix);
    if (!hit) return;
    if (last >= 0 && ix.hunk[i] !== ix.hunk[last]) out.push("…");
    out.push(`${l.kind}${l.text}`);
    last = i;
  });
  return out;
}
