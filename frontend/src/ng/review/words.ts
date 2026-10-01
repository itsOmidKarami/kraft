/** A character range `[start, end)` in one line. */
export type Range = [number, number];

/** Lines longer than this get no word ranges: the diff is about the line. */
export const MAX_LINE = 500;
/** Below this share of the shorter line left unchanged, the pair is a rewrite. */
const MIN_KEPT = 0.3;

const TOKEN = /\w+|\s+|[^\w\s]/g;

/** The changed middle of a paired removed and added line (R22): their common
 *  prefix and suffix, in word, space and punctuation tokens, are trimmed and
 *  what is left on each side is the change. Nothing for an identical pair, a
 *  long line, or a rewrite. */
export function changedRanges(a: string, b: string): { old: Range[]; new: Range[] } {
  const none = { old: [], new: [] };
  if (a === b || a.length > MAX_LINE || b.length > MAX_LINE) return none;
  const ta = a.match(TOKEN) ?? [];
  const tb = b.match(TOKEN) ?? [];
  let p = 0;
  while (p < ta.length && p < tb.length && ta[p] === tb[p]) p++;
  let s = 0;
  while (s < ta.length - p && s < tb.length - p && ta[ta.length - 1 - s] === tb[tb.length - 1 - s]) s++;
  const len = (ts: string[]) => ts.reduce((n, t) => n + t.length, 0);
  const pre = len(ta.slice(0, p));
  const sufA = len(ta.slice(ta.length - s));
  const sufB = len(tb.slice(tb.length - s));
  if (pre + sufA < MIN_KEPT * Math.min(a.length, b.length)) return none;
  const range = (line: string, suf: number): Range[] => (pre < line.length - suf ? [[pre, line.length - suf]] : []);
  return { old: range(a, sufA), new: range(b, sufB) };
}
