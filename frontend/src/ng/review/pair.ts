import type { Hunk, PatchLine } from "./patch";

/** One row of a side-by-side hunk: the old line on the left, the new one on
 *  the right, either missing. A context line is both. */
export interface SplitRow {
  left: PatchLine | null;
  right: PatchLine | null;
}

/** Pairs a hunk's lines for the split view and the word highlight (R22,
 *  prototype `pairSplit`): within a run of changes the k-th removed line
 *  faces the k-th added one; the longer side's extras face nothing. */
export function pairRows(hunk: Hunk): SplitRow[] {
  const out: SplitRow[] = [];
  const ls = hunk.lines;
  let i = 0;
  while (i < ls.length) {
    if (ls[i].kind === " ") {
      out.push({ left: ls[i], right: ls[i] });
      i++;
      continue;
    }
    const del: PatchLine[] = [];
    const add: PatchLine[] = [];
    while (i < ls.length && ls[i].kind === "-") del.push(ls[i++]);
    while (i < ls.length && ls[i].kind === "+") add.push(ls[i++]);
    for (let k = 0; k < Math.max(del.length, add.length); k++) out.push({ left: del[k] ?? null, right: add[k] ?? null });
  }
  return out;
}
