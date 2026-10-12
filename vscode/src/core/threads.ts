import { drafts } from "../../../frontend/src/ng/review/finish";
import type { ReviewComment, ReviewThread, Suggestion, ThreadIn, ThreadLabel } from "./api";

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

export const LABEL_TEXT: Record<ThreadLabel, string> = { must_fix: "Must fix", question: "Question", nit: "Nit" };

/** The thread's title: "Draft", its label, and a submitted thread's state past open. */
export function threadLabel(t: ReviewThread): string | undefined {
  const parts = [
    t.draft && "Draft",
    t.label && LABEL_TEXT[t.label],
    !t.draft && t.state === "claimed" && "Claimed",
    !t.draft && t.state === "resolved" && "Resolved",
  ].filter((p): p is string => !!p);
  return parts.length ? parts.join(" · ") : undefined;
}

/** A draft can be relabelled; a submitted thread resolved or reopened (the server 409s a draft's). */
export function threadContext(t: ReviewThread): string {
  const can = t.draft ? ["can-label"] : t.state === "resolved" ? ["can-reopen"] : ["can-resolve"];
  return `kraft-thread:${can.join(",")}`;
}

/** Only your unsubmitted comments change. */
export function commentContext(_t: ReviewThread, c: ReviewComment): string {
  return c.draft && c.author === "you" ? "kraft-comment:mine-draft" : "kraft-comment:";
}

export function authorName(c: ReviewComment): string {
  return c.author === "you" ? (c.draft ? "You (draft)" : "You") : c.author;
}

const fence = (text: string) => "`".repeat(Math.max(3, ...[...text.matchAll(/`+/g)].map((m) => m[0].length + 1)));

/** The body, then a web-UI suggestion as a fenced block, then an agent's claim. */
export function commentMarkdown(c: ReviewComment): string {
  const parts = [c.body];
  const s = c.suggestion;
  if (s) {
    const f = fence(s.replacement);
    parts.push(`Suggested replacement for lines ${s.start_line}-${s.end_line}:\n\n${f}\n${s.replacement}\n${f}`);
  }
  if (c.claim) parts.push(`_${c.claim}_`);
  return parts.join("\n\n");
}

/** A draft thread's first comment is the thread's own body: it changes through the thread route. */
export type WriteTarget = { kind: "thread"; tid: string } | { kind: "comment"; cid: string };

export function writeTarget(t: ReviewThread, c: ReviewComment): WriteTarget {
  return t.draft && t.comments[0]?.id === c.id ? { kind: "thread", tid: t.id } : { kind: "comment", cid: c.id };
}

/** The server replaces a comment's suggestion with the request's: send the one it has back. */
export function commentPatch(c: ReviewComment, body: string): { body: string; suggestion: Suggestion | null } {
  return { body, suggestion: c.suggestion };
}

/**
 * What Discard deletes: draft threads whole, your draft replies on submitted
 * threads one by one. `count` is every draft comment, wherever it was written.
 */
export function discardPlan(threads: ReviewThread[]): { threads: string[]; comments: string[]; count: number } {
  const all = drafts(threads);
  return {
    threads: threads.filter((t) => t.draft).map((t) => t.id),
    comments: all.filter((d) => !d.thread.draft).map((d) => d.comment.id),
    count: all.length,
  };
}

export const WHOLE_HEADING = "On the whole change";
export const ELSEWHERE_HEADING = "Threads on files not in this comparison";

const firstLine = (body: string) => body.trim().split("\n")[0];

/** The document listing threads not drawn on a file; `lineOf` is each thread's zero-based line. */
export function otherThreadsText(id: string, threads: ReviewThread[], places: ReadonlyMap<string, Place>): { text: string; lineOf: Map<string, number> } {
  const lines = [`# ${id}: threads not on a file in this review`];
  const lineOf = new Map<string, number>();
  for (const [kind, heading] of [["whole", WHOLE_HEADING], ["elsewhere", ELSEWHERE_HEADING]] as const) {
    const group = threads.filter((t) => places.get(t.id)?.kind === kind);
    if (!group.length) continue;
    lines.push("", `## ${heading}`, "");
    for (const t of group) {
      lineOf.set(t.id, lines.length);
      const where = kind === "whole" ? "" : `${t.file_path}${t.start_line ? `:${t.start_line}${t.side === "old" ? " (base)" : ""}` : ""}: `;
      lines.push(`- ${where}${firstLine(t.comments[0]?.body ?? "")}`);
    }
  }
  return { text: `${lines.join("\n")}\n`, lineOf };
}

/** What to do to the drawn threads: draw the new, redraw the moved, refresh the rest, drop the gone. */
export function syncPlan<T extends { id: string }>(drawn: ReadonlyMap<string, string>, next: readonly T[], keyOf: (t: T) => string) {
  const plan = { create: [] as T[], update: [] as T[], remove: [] as string[] };
  const seen = new Set<string>();
  for (const t of next) {
    seen.add(t.id);
    const was = drawn.get(t.id);
    if (was === undefined) plan.create.push(t);
    else if (was !== keyOf(t)) plan.remove.push(t.id), plan.create.push(t);
    else plan.update.push(t);
  }
  for (const tid of drawn.keys()) if (!seen.has(tid)) plan.remove.push(tid);
  return plan;
}
