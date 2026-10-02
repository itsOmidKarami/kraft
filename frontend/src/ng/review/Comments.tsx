import { useRef, useState, type ReactNode } from "react";
import type { Compare, CompareFile, ReviewThread } from "../../types";
import { detailOf, jsonBody, request } from "../http";
import { Composer, targetKey, type Draft, type Target } from "./Composer";
import { pickRange, type Pick } from "./DiffView";
import type { PatchFile } from "./patch";
import type { Anchor } from "./rows";
import { Thread } from "./Thread";

/** The new-side text of lines `a..b` of a file, from the patch. */
export function newLines(pf: PatchFile | undefined, a: number, b: number): string[] {
  if (!pf) return [];
  return pf.hunks.flatMap((h) => h.lines).filter((l) => l.new !== null && l.new >= a && l.new <= b).map((l) => l.text);
}

/** A thread's place in the diff: under its last line on its side, or the file's top. */
const placeOf = (t: ReviewThread) => (t.start_line === null ? `${t.file_path}|top` : `${t.file_path}|${t.side}|${t.end_line}`);

/** Threads and the composer, placed in the diff (W8 F). `after` and `top`
 *  feed DiffView's slots; `elsewhere` lists the threads with no place in what
 *  is drawn (another file, a line outside the hunks). */
export function useComments({ itemId, compare, files, patch, threads, reload, onClose }: {
  itemId: string;
  compare: Compare | null;
  /** The files drawn. */
  files: CompareFile[];
  patch: Map<string, PatchFile>;
  threads: ReviewThread[];
  reload: () => void;
  /** The composer closed, sent or cancelled: the page drops the pick it was opened on. */
  onClose?: (t: Target) => void;
}) {
  const drafts = useRef(new Map<string, Draft>()).current;
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
  for (const t of threads) {
    if (open?.editing?.id === t.id) continue;
    const k = placeOf(t);
    if (t.file_path && visible.has(k)) at.set(k, [...(at.get(k) ?? []), t]);
    else elsewhere.push(t);
  }

  const submit = async (target: Target, editing: ReviewThread | null, d: Draft): Promise<string | null> => {
    const r = target.range;
    const suggestion = d.suggest !== null && r ? { start_line: r.start, end_line: r.end, replacement: d.suggest } : null;
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

  const composer = (o: NonNullable<typeof open>) => (
    <Composer
      key={targetKey(o.target)}
      target={o.target}
      lines={o.target.range ? newLines(patch.get(o.target.path), o.target.range.start, o.target.range.end) : []}
      drafts={drafts}
      editing={o.editing}
      onSubmit={(d) => submit(o.target, o.editing, d)}
      onCancel={() => {
        setOpen(null);
        onClose?.(o.target);
      }}
    />
  );
  const card = (t: ReviewThread) => (
    <Thread
      key={t.id}
      thread={t}
      oldLines={(a, b) => newLines(patch.get(t.file_path ?? ""), a, b)}
      onChanged={reload}
      onEdit={(e) => setOpen({ editing: e, target: { path: e.file_path ?? "", range: e.start_line === null ? null : { side: e.side!, start: e.start_line, end: e.end_line! } } })}
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
    elsewhere: elsewhere.length ? <section className="rv-elsewhere" aria-label="Threads on files not in this comparison"><h2>Threads on files not in this comparison</h2>{elsewhere.map(card)}</section> : null,
    openPick: (p: Pick) => {
      const [start, end] = pickRange(p);
      setOpen({ editing: null, target: { path: p.path, range: { side: p.side, start, end } } });
    },
    openFile: (path: string) => setOpen({ editing: null, target: { path, range: null } }),
  };
}
