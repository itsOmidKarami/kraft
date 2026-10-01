/** A file's diff as rows to draw (W8 E): hunks, lines with their syntax
 *  tokens and changed-word ranges, in the unified or the split layout. Pure. */
import { pairRows } from "./pair";
import type { LineKind, PatchFile, PatchLine } from "./patch";
import { tokenizeSide, type Lang, type Token } from "./tokenize";
import { changedRanges, type Range } from "./words";

export type Side = "old" | "new";

export interface Cell {
  kind: LineKind;
  old: number | null;
  new: number | null;
  text: string;
  tokens: Token[];
  /** Changed-word ranges, when the line is half of an edited pair. */
  ranges: Range[];
}

/** The line a row stands for when picked: a removed line's old number, else the new one. */
export interface Anchor {
  side: Side;
  line: number;
}

export type Row =
  | { t: "hunk"; text: string }
  | { t: "u"; cell: Cell; at: Anchor }
  | { t: "s"; left: Cell | null; right: Cell | null; at: Anchor };

export function buildRows(file: PatchFile, layout: "unified" | "split", lang: Lang, words: boolean): Row[] {
  const rows: Row[] = [];
  for (const h of file.hunks) {
    rows.push({ t: "hunk", text: h.header });
    const oldSide = h.lines.filter((l) => l.kind !== "+");
    const newSide = h.lines.filter((l) => l.kind !== "-");
    const tok = new Map<PatchLine, Token[]>();
    tokenizeSide(oldSide.map((l) => l.text), lang).forEach((t, i) => tok.set(oldSide[i], t));
    // A context line takes its new-side tokens; both sides read the same there.
    tokenizeSide(newSide.map((l) => l.text), lang).forEach((t, i) => tok.set(newSide[i], t));
    const pairs = pairRows(h);
    const ranges = new Map<PatchLine, Range[]>();
    if (words)
      for (const { left, right } of pairs)
        if (left?.kind === "-" && right?.kind === "+") {
          const r = changedRanges(left.text, right.text);
          ranges.set(left, r.old);
          ranges.set(right, r.new);
        }
    const cell = (l: PatchLine): Cell => ({ ...l, tokens: tok.get(l) ?? [], ranges: ranges.get(l) ?? [] });
    const at = (l: PatchLine): Anchor => (l.kind === "-" ? { side: "old", line: l.old! } : { side: "new", line: l.new! });
    if (layout === "unified") for (const l of h.lines) rows.push({ t: "u", cell: cell(l), at: at(l) });
    else for (const { left, right } of pairs) rows.push({ t: "s", left: left && cell(left), right: right && cell(right), at: at(right ?? left!) });
  }
  return rows;
}

/** The anchors a row carries, for placing threads after it: a context line is on both sides. */
export function anchorsOf(row: Row): Anchor[] {
  if (row.t === "hunk") return [];
  const cells = row.t === "u" ? [row.cell] : [row.left, row.right];
  const out: Anchor[] = [];
  for (const c of cells) {
    if (!c) continue;
    if (c.old !== null && !out.some((a) => a.side === "old" && a.line === c.old)) out.push({ side: "old", line: c.old });
    if (c.new !== null && !out.some((a) => a.side === "new" && a.line === c.new)) out.push({ side: "new", line: c.new });
  }
  return out;
}

/** Spans for one line: its tokens, cut where a changed-word range starts or ends. */
export function spans(cell: Pick<Cell, "text" | "tokens" | "ranges">): { text: string; cls?: string; changed: boolean }[] {
  const toks = cell.tokens.length ? cell.tokens : [{ text: cell.text }];
  const out: { text: string; cls?: string; changed: boolean }[] = [];
  let pos = 0;
  const inRange = (i: number) => cell.ranges.some(([a, b]) => i >= a && i < b);
  for (const t of toks) {
    let start = 0;
    for (let i = 1; i <= t.text.length; i++) {
      if (i === t.text.length || inRange(pos + i) !== inRange(pos + start)) {
        out.push({ text: t.text.slice(start, i), cls: t.cls, changed: inRange(pos + start) });
        start = i;
      }
    }
    pos += t.text.length;
  }
  return out;
}
