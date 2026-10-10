/** A file's diff drawn with the unchanged lines its hunks leave out (the hunk rows' arrows). Pure. */
import type { Hunk, PatchFile, PatchLine } from "./patch";

/** How many lines one press of an arrow shows. */
export const STEP = 20;

/** A run of the whole file's diff lines that is drawn: from the first, up to and not including the second. */
export type Span = [number, number];
/** `up` grows the hunk after a gap, `down` the hunk before it, `all` closes the gap. */
export type Grow = "up" | "down" | "all";

export interface Whole {
  /** Every line of the file, as a diff with the whole file for context gives them. */
  lines: PatchLine[];
  spans: Span[];
}

const sameLine = (a: PatchLine | undefined, b: PatchLine | undefined) => !!a && !!b && a.old === b.old && a.new === b.new;

/** Where each hunk of `pf` sits in `whole`'s lines, or null when the two no longer agree (the file moved between the two reads). */
export function spansOf(pf: PatchFile, whole: PatchFile): Whole | null {
  const lines = whole.hunks.flatMap((h) => h.lines);
  const spans: Span[] = [];
  for (const h of pf.hunks) {
    const at = lines.findIndex((l) => sameLine(l, h.lines[0]));
    const end = at + h.lines.length;
    if (at < 0 || !sameLine(lines[end - 1], h.lines[h.lines.length - 1])) return null;
    spans.push([at, end]);
  }
  return { lines, spans };
}

/** `spans` with the gap before span `gap` opened by `how`; `gap` is `spans.length` for the lines after the last one. Spans that meet become one. */
export function grow(spans: Span[], gap: number, how: Grow, total: number): Span[] {
  // A press made on an older drawing, for a gap that has since closed.
  if (gap < 0 || gap > spans.length) return spans;
  const next = spans.map((s): Span => [...s]);
  const lo = gap > 0 ? next[gap - 1][1] : 0;
  const hi = gap < next.length ? next[gap][0] : total;
  if (gap < next.length && how !== "down") next[gap][0] = how === "all" ? lo : Math.max(lo, hi - STEP);
  else if (gap > 0) next[gap - 1][1] = how === "all" ? hi : Math.min(hi, lo + STEP);
  return next.reduce<Span[]>((out, s) => {
    const last = out[out.length - 1];
    if (last && last[1] >= s[0]) last[1] = s[1];
    else out.push(s);
    return out;
  }, []);
}

/** `pf` with `w`'s spans for hunks. A span no arrow touched is its own hunk still, header and all. */
export function widen(pf: PatchFile, w: Whole): PatchFile {
  const hunks = w.spans.map(([a, b]): Hunk => {
    const lines = w.lines.slice(a, b);
    const olds = lines.filter((l) => l.old !== null);
    const news = lines.filter((l) => l.new !== null);
    const oldStart = olds[0]?.old ?? 0;
    const newStart = news[0]?.new ?? 0;
    const own = pf.hunks.find((h) => h.lines.length === lines.length && sameLine(h.lines[0], lines[0]));
    return own ?? { header: `@@ -${oldStart},${olds.length} +${newStart},${news.length} @@`, oldStart, newStart, lines };
  });
  return { ...pf, hunks, rest: w.lines.length - (w.spans[w.spans.length - 1]?.[1] ?? 0) };
}

/** Git's own context around a change, which `/compare` leaves alone. */
const CONTEXT = 3;

/** The lines after `pf`'s last hunk: counted once the whole file was read. Before that, 0 when the hunk
 *  stops short of git's context after its last change (the file ends there), else null: not known. */
function rest(pf: PatchFile): number | null {
  if (pf.rest !== undefined) return pf.rest;
  const last = pf.hunks[pf.hunks.length - 1]?.lines ?? [];
  let trailing = 0;
  while (trailing < last.length && last[last.length - 1 - trailing].kind === " ") trailing++;
  return trailing < CONTEXT ? 0 : null;
}

/** Unchanged lines not drawn: `count` of them from the new side's line `from`. A null count is not known yet. */
export interface Gap {
  from: number;
  count: number | null;
}

/** The gap before each hunk of `pf` (from the hunk before it, or the file's first line), then the one after its last hunk. */
export function gapsOf(pf: PatchFile): Gap[] {
  let next = 1;
  const before = pf.hunks.map((h): Gap => {
    const gap = { from: next, count: h.newStart - next };
    next = h.newStart + h.lines.filter((l) => l.new !== null).length;
    return gap;
  });
  return [...before, { from: next, count: rest(pf) }];
}
