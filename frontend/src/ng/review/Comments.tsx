import { useRef, useState, type ReactNode } from "react";
import type { Compare, CompareFile, ReviewThread } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { Composer, targetKey, type Draft, type Target } from "./Composer";
import { rangeOfPick, type Pick } from "./DiffView";
import { unresolved } from "./model";
import type { PatchFile } from "./patch";
import { isMixed, isOneLine, lineIndex, quoteOf, rangeName, startSideOf, type LineRange } from "./range";
import type { Anchor } from "./rows";
import { Thread, threadRange } from "./Thread";

/** At most this many lines are quoted with a comment. */
const QUOTE_LINES = 200;

/** The new-side text of lines `a..b` of a file, from the patch. */
export function newLines(pf: PatchFile | undefined, a: number, b: number): string[] {
  if (!pf) return [];
  return pf.hunks.flatMap((h) => h.lines).filter((l) => l.new !== null && l.new >= a && l.new <= b).map((l) => l.text);
}

/** A thread's place in the diff: under its last line on its side, or the file's top. */
const placeOf = (t: ReviewThread) => (t.start_line === null ? `${t.file_path}|top` : `${t.file_path}|${t.side}|${t.end_line}`);

/** Threads and the composer, placed in the diff (W8 F). `after` and `top`
 *  feed DiffView's slots; `whole` lists the threads on no file, and
 *  `elsewhere` those with no place in what is drawn (another file, a line
 *  outside the hunks). */
export function useComments({ itemId, compare, files, patch, threads, reload, onClose, onRetarget }: {
  itemId: string;
  compare: Compare | null;
  /** The files drawn. */
  files: CompareFile[];
  patch: Map<string, PatchFile>;
  threads: ReviewThread[];
  reload: () => void;
  /** The composer closed, sent or cancelled: the page drops the pick it was opened on. */
  onClose?: (t: Target) => void;
  /** The composer's own × moved its range: the page moves the pick with it. */
  onRetarget?: (t: Target) => void;
}) {
  const drafts = useRef(new Map<string, Draft>()).current;
  const indexes = useRef(new WeakMap<PatchFile, ReturnType<typeof lineIndex>>()).current;
  const indexOf = (path: string) => {
    const pf = patch.get(path);
    if (!pf) return lineIndex(undefined);
    if (!indexes.has(pf)) indexes.set(pf, lineIndex(pf));
    return indexes.get(pf)!;
  };
  const [open, setOpen] = useState<{ target: Target; editing: ReviewThread | null } | null>(null);

  const visible = new Set<string>();
  for (const f of files) {
    visible.add(`${f.path}|top`);
    for (const h of patch.get(f.path)?.hunks ?? [])
      for (const l of h.lines) {
        if (l.old !== null) visible.add(`${f.path}|old|${l.old}`);
        if (l.new !== null) visible.add(`${f.path}|new|${l.new}`);
      }
  }
  const at = new Map<string, ReviewThread[]>();
  const elsewhere: ReviewThread[] = [];
  // A thread on no file (`kraft item comment --body` alone) is about the whole change, not a file missing here (R8b-08).
  const whole: ReviewThread[] = [];
  for (const t of threads) {
    if (open?.editing?.id === t.id) continue;
    const k = placeOf(t);
    if (!t.file_path) whole.push(t);
    else if (visible.has(k)) at.set(k, [...(at.get(k) ?? []), t]);
    else elsewhere.push(t);
  }

  const submit = async (target: Target, editing: ReviewThread | null, d: Draft): Promise<string | null> => {
    const r = target.range;
    // A suggestion left as the lines it was filled from changes nothing: it is not sent.
    const changes = r && d.suggest !== null && d.suggest !== newLines(patch.get(target.path), r.start, r.end).join("\n");
    // Only on new lines: one set aside on a range across sides, or on old lines, is not sent.
    const suggestion = changes && r.side === "new" && !isMixed(r) ? { start_line: r.start, end_line: r.end, replacement: d.suggest! } : null;
    const quote = r ? quoteOf(r, indexOf(target.path)) : [];
    let res;
    if (editing) res = await request(`/threads/${editing.id}`, jsonBody("PATCH", { body: d.body.trim(), label: d.label, suggestion }));
    else {
      const file = files.find((f) => f.path === target.path);
      res = await request(
        `/work-items/${encodeURIComponent(itemId)}/threads`,
        jsonBody("POST", {
          body: d.body.trim(),
          file_path: target.path,
          label: d.label,
          ...(r && { side: r.side, start_line: r.start, end_line: r.end }),
          ...(r && isMixed(r) && { start_side: r.startSide }),
          // The lines as the diff showed them, for whoever reads the thread later, off this page.
          ...(quote.length > 0 && { quote: (quote.length > QUOTE_LINES ? [...quote.slice(0, QUOTE_LINES), "…"] : quote).join("\n") }),
          ...(suggestion && { suggestion }),
          // The node only when one node touched the file: the server reads it as the thread's owner.
          ...(file?.touched_by.length === 1 && { node_id: file.touched_by[0] }),
          // Off `latest` (the working tree, no sha) the lines are the compared commit's, not HEAD's.
          ...(compare?.to.sha && { anchor_sha: compare.to.sha }),
        }),
      );
    }
    if (res.status < 200 || res.status >= 300) return detailOf(res.body);
    setOpen(null);
    onClose?.(target);
    reload();
    return null;
  };

  /** The open composer moves to `range`, its text with it: a suggested change typed for the old lines is kept,
   *  and the composer says so (it sets it aside on a range across sides) instead of dropping it unsaid (R10b-09). */
  const moveTo = (o: NonNullable<typeof open>, range: LineRange) => {
    const r = o.target.range!;
    const target = { path: o.target.path, range };
    const d = drafts.get(targetKey(o.target));
    drafts.delete(targetKey(o.target));
    // Moved back onto the lines it was written for, it is in place again: no note (R11b-06).
    const wrote = d?.wrote ?? rangeName(r);
    if (d) drafts.set(targetKey(target), d.suggest === null ? d : { ...d, wrote: rangeName(range) === wrote ? undefined : wrote });
    setOpen({ ...o, target });
    return target;
  };
  const sameRange = (a: LineRange, b: LineRange) => a.side === b.side && a.start === b.start && a.end === b.end && startSideOf(a) === startSideOf(b);
  const composer = (o: NonNullable<typeof open>) => (
    <Composer
      key={targetKey(o.target)}
      target={o.target}
      lines={o.target.range ? newLines(patch.get(o.target.path), o.target.range.start, o.target.range.end) : []}
      onCollapse={() => {
        const r = o.target.range!;
        onRetarget?.(moveTo(o, { side: r.side, start: r.end, end: r.end }));
      }}
      drafts={drafts}
      editing={o.editing}
      onSubmit={(d) => submit(o.target, o.editing, d)}
      onCancel={() => {
        setOpen(null);
        onClose?.(o.target);
      }}
    />
  );
  // A range's lines, quoted on its thread: as they were when it was written, else as the diff shows them now.
  const quoteFor = (t: ReviewThread) => {
    const r = threadRange(t);
    if (!r || isOneLine(r)) return null;
    return t.quote ? t.quote.split("\n") : quoteOf(r, indexOf(t.file_path ?? ""));
  };
  const card = (t: ReviewThread) => (
    <Thread
      key={t.id}
      thread={t}
      quote={quoteFor(t)}
      oldLines={(a, b) => newLines(patch.get(t.file_path ?? ""), a, b)}
      onChanged={reload}
      onEdit={(e) => setOpen({ editing: e, target: { path: e.file_path ?? "", range: threadRange(e) } })}
    />
  );
  const openKey = open && (open.target.range ? `${open.target.path}|${open.target.range.side}|${open.target.range.end}` : `${open.target.path}|top`);
  const slot = (k: string): ReactNode => {
    const ts = at.get(k) ?? [];
    if (!ts.length && openKey !== k) return null;
    return <>{ts.map(card)}{openKey === k && composer(open!)}</>;
  };

  return {
    after: (path: string, a: Anchor) => slot(`${path}|${a.side}|${a.line}`),
    top: (path: string) => slot(`${path}|top`),
    whole: whole.length ? <section className="rv-elsewhere rv-whole" aria-label="On the whole change"><h2>On the whole change</h2>{whole.map(card)}</section> : null,
    elsewhere: elsewhere.length ? <section className="rv-elsewhere" aria-label="Threads on files not in this comparison"><h2>Threads on files not in this comparison</h2>{elsewhere.map(card)}</section> : null,
    /** The pick the composer was opened on was edited (a handle, Shift-click, Shift+arrows): the composer follows, with its text. */
    retargetTo: (p: Pick, from: Pick | null) => {
      const r = open?.target.range;
      if (!open || open.editing || !r || open.target.path !== p.path || !from || from.path !== p.path) return;
      // Only the range the composer sits on: a pick made elsewhere since is not its to move.
      if (!sameRange(rangeOfPick(from, indexOf(p.path)), r)) return;
      moveTo(open, rangeOfPick(p, indexOf(p.path)));
    },
    openPick: (p: Pick) => setOpen({ editing: null, target: { path: p.path, range: rangeOfPick(p, indexOf(p.path)) } }),
    /** The ranges of a file's open threads, for the diff to shade. */
    commented: (path: string): LineRange[] =>
      threads.flatMap((t) => {
        const r = t.file_path === path && unresolved(t) && open?.editing?.id !== t.id ? threadRange(t) : null;
        return r ? [r] : [];
      }),
    openFile: (path: string) => setOpen({ editing: null, target: { path, range: null } }),
  };
}
