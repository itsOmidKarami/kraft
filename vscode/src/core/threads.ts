import type { ReviewThread, ThreadIn, ThreadLabel } from "./api";

/** Which side of the diff a document shows: the base (`kraft-git`) or the branch (`kraft-wt`). */
export type Side = "old" | "new";

export function sideOfScheme(scheme: string): Side | undefined {
  return scheme === "kraft-git" ? "old" : scheme === "kraft-wt" ? "new" : undefined;
}

/** Zero-based, inclusive. */
export type Lines = { start: number; end: number };

/**
 * Where a thread is drawn. A `file` place's `file` is the path its document
 * shows: a renamed file's old side is the old path.
 */
export type Place = { kind: "file"; file: string; side: Side; lines: Lines } | { kind: "whole" } | { kind: "elsewhere" };

/** The files a review shows, and its renames (new path → old path). */
export interface Shown { files: readonly string[]; renames?: ReadonlyMap<string, string> }

const across = (t: ReviewThread) => !!t.start_side && t.start_side !== t.side;

export function placeThread(t: ReviewThread, shown: Shown): Place {
  if (!t.file_path) return { kind: "whole" };
  if (!shown.files.includes(t.file_path)) return { kind: "elsewhere" };
  const side: Side = t.side ?? "new";
  // The server stores a rename under its new path; its old side is the old path's lines.
  const file = side === "old" ? (shown.renames?.get(t.file_path) ?? t.file_path) : t.file_path;
  if (t.start_line === null) return { kind: "file", file, side, lines: { start: 0, end: 0 } };
  const end = (t.end_line ?? t.start_line) - 1;
  // A range across sides is one diff run, not one document's lines: drawn on its end alone.
  return { kind: "file", file, side, lines: { start: across(t) ? end : t.start_line - 1, end } };
}

export function placeKey(p: Place): string {
  return p.kind === "file" ? `${p.file}|${p.side}|${p.lines.start}|${p.lines.end}` : p.kind;
}

export function clampLines(l: Lines, lineCount: number): Lines {
  const end = Math.min(l.end, Math.max(0, lineCount - 1));
  return { start: Math.min(l.start, end), end };
}

/** What a loaded document can say about a place: its line count and its lines. */
export interface DocLines { lineCount: number; lineAt(n: number): string }

/**
 * Whether a one-side thread's `quote` (each line led by its diff mark) still
 * reads at its lines. A quote clipped by the server ends in `…`: compared as a
 * prefix. With no quote, or a range across sides, there is nothing to compare.
 */
export function quoteFits(t: ReviewThread, lines: Lines, doc: DocLines): boolean {
  if (!t.quote || across(t) || t.start_line === null) return true;
  const q = t.quote.split("\n");
  for (let i = 0; i < q.length && lines.start + i <= lines.end; i++) {
    if (q[i] === "…") break;
    const want = q[i].slice(1);
    const got = doc.lineAt(lines.start + i).replace(/\r$/, "");
    const clipped = i === q.length - 1 && want.endsWith("…") && got.startsWith(want.slice(0, -1));
    if (got !== want && !clipped) return false;
  }
  return true;
}

/**
 * A file place against its loaded document: past the end, drawn on the last
 * line; lines that no longer read as quoted, not on the file in this
 * comparison. Only ever called with a loaded document.
 */
export function fitPlace(t: ReviewThread, p: Place, doc: DocLines): Place {
  if (p.kind !== "file") return p;
  if (p.lines.end >= doc.lineCount) return { ...p, lines: clampLines(p.lines, doc.lineCount) };
  return quoteFits(t, p.lines, doc) ? p : { kind: "elsewhere" };
}

type Pos = { line: number; character: number };

/** A selection ending at column 0 of a later line stops on the line before. */
export function selectionLines(start: Pos, end: Pos): Lines {
  return { start: start.line, end: end.character === 0 && end.line > start.line ? end.line - 1 : end.line };
}

/**
 * A new thread on a review document. Always the NEW path: on a renamed file's
 * old side, `file` is the old path and `renames` (new → old) maps it back.
 * `undefined` for a document that is not a side of the review.
 */
export function toThreadIn(
  scheme: string,
  file: string,
  lines: Lines,
  body: string,
  label: ThreadLabel | null,
  renames?: ReadonlyMap<string, string>,
): ThreadIn | undefined {
  const side = sideOfScheme(scheme);
  if (!side) return undefined;
  const path = side === "old" ? ([...(renames ?? [])].find(([, old]) => old === file)?.[0] ?? file) : file;
  return { body, file_path: path, side, start_line: lines.start + 1, end_line: lines.end + 1, ...(label && { label }) };
}
