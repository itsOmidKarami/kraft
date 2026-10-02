import { Link } from "react-router-dom";
import { useEffect, useId, useRef, useState } from "react";
import * as api from "../../api";
import { plainMarkdown } from "../../format";
import type { DiffFile } from "../../types";
import { Button } from "../ui/Button";
import { act } from "./actions";
import { sendOnModEnter } from "../keys";

/** The title, edited in place (Decisions §2): Enter saves, Esc restores. */
export function Title({ id, title, onSaved }: { id: string; title: string; onSaved: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(title);
  const [error, setError] = useState<string | null>(null);
  const hint = useId();
  useEffect(() => setText(title), [title]);
  const stop = () => {
    setEditing(false);
    setText(title);
    setError(null);
  };
  const save = async () => {
    const t = text.trim();
    if (!t) return setError("A title can't be blank.");
    if (t === title) return stop();
    const r = await act.patch(id, { title: t });
    if (!r.ok) return setError(r.error);
    setEditing(false);
    onSaved();
  };
  if (!editing)
    return (
      <h1 className="item-title">
        <button type="button" className="item-title-btn" title="Rename" onClick={() => setEditing(true)}>{title}</button>
      </h1>
    );
  return (
    <div className="item-title-edit">
      <h1 className="item-visually-hidden">{title}</h1>
      <input
        aria-label="Title"
        aria-describedby={hint}
        className="item-title-input"
        autoFocus
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void save();
          if (e.key === "Escape") { e.stopPropagation(); stop(); }
        }}
        onBlur={stop}
      />
      <span id={hint} className="item-muted">{error ? <span className="item-error" role="alert">{error}</span> : "Enter to save · Esc"}</span>
    </div>
  );
}

/** The brief: two lines, "more · edit"; edit is a field in the same spot (Decisions §2). */
export function Brief({ id, brief, onSaved }: { id: string; brief: string; onSaved: () => void }) {
  const [more, setMore] = useState(false);
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(brief);
  const [error, setError] = useState<string | null>(null);
  const [long, setLong] = useState(false);
  const [busy, setBusy] = useState(false);
  const p = useRef<HTMLParagraphElement>(null);
  useEffect(() => setText(brief), [brief]);
  // "more" only when two lines do not hold it.
  useEffect(() => {
    const el = p.current;
    if (el && !more) setLong(el.scrollHeight > el.clientHeight + 1);
  }, [brief, more, editing]);
  const save = async () => {
    setBusy(true);
    const r = await act.patch(id, { description: text });
    setBusy(false);
    if (!r.ok) return setError(r.error);
    setEditing(false);
    onSaved();
  };
  const send = sendOnModEnter(save, !busy);
  if (editing)
    return (
      <div className="item-brief-edit">
        <textarea aria-label="Brief" className="item-input" rows={4} autoFocus value={text} onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") { e.stopPropagation(); setEditing(false); setText(brief); } else send(e); }} />
        {error && <p className="item-error" role="alert">{error}</p>}
        <div className="item-actions">
          <span className="item-muted">The next agent to launch reads the new brief.</span>
          <Button onClick={() => { setEditing(false); setText(brief); setError(null); }}>Cancel</Button>
          <Button variant="primary" disabled={busy} onClick={save}>Save</Button>
        </div>
      </div>
    );
  return (
    <div className="item-brief">
      {brief ? <p ref={p} className={more ? "item-brief-text is-open" : "item-brief-text"}>{more ? brief : plainMarkdown(brief)}</p> : <p className="item-brief-text item-muted">No brief.</p>}
      <div className="item-brief-links">
        {(long || more) && <button type="button" className="item-link" aria-expanded={more} onClick={() => setMore((m) => !m)}>{more ? "less" : "more"}</button>}
        {(long || more) && <span aria-hidden> · </span>}
        <button type="button" className="item-link" onClick={() => setEditing(true)}>edit</button>
      </div>
    </div>
  );
}

/** The item's changed files, landed (base..HEAD) and in flight (HEAD..worktree), one row per path. */
export function useDiffFiles(id: string, version: string): DiffFile[] | null {
  const [files, setFiles] = useState<DiffFile[] | null>(null);
  useEffect(() => {
    // Read again on every read of the item: only the newest read may land.
    let live = true;
    api.getWorkItemDiff(id).then((d) => {
      if (!live) return;
      const by = new Map<string, DiffFile>();
      for (const f of [...(d.landed?.files ?? []), ...d.files]) {
        const was = by.get(f.path);
        by.set(f.path, was ? { ...f, insertions: was.insertions + f.insertions, deletions: was.deletions + f.deletions } : f);
      }
      setFiles([...by.values()]);
    }, () => live && setFiles(null));
    return () => { live = false; };
  }, [id, version]);
  return files;
}

export const totals = (files: DiffFile[]) => ({ add: files.reduce((a, f) => a + f.insertions, 0), del: files.reduce((a, f) => a + f.deletions, 0) });

/** `N files +A −D · Review changes` (the prototype's diff line); hidden with no diff. */
export function DiffLine({ id, version }: { id: string; version: string }) {
  const files = useDiffFiles(id, version);
  if (!files?.length) return null;
  const { add, del } = totals(files);
  return (
    <p className="item-diffline">
      {files.length} {files.length === 1 ? "file" : "files"} <span className="item-add">+{add}</span> <span className="item-del">−{del}</span>
      <span aria-hidden> · </span>
      <Link className="item-link is-strong" to={`/work-items/${encodeURIComponent(id)}/review`}>Review changes</Link>
    </p>
  );
}
