import { useEffect, useRef, useState } from "react";
import type { ReviewThread, ThreadLabel } from "../../types";
import { Button } from "../ui/Button";
import { Markdown } from "../ui/Markdown";
import type { Side } from "./rows";
import { LABELS, codeBlock } from "./Thread";

/** Where a comment goes: a line range on one side of a file, or the whole file. */
export interface Target {
  path: string;
  range: { side: Side; start: number; end: number } | null;
}
export const targetKey = (t: Target) => (t.range ? `${t.path}|${t.range.side}|${t.range.start}-${t.range.end}` : `${t.path}|file`);

/** The composer's text until Add to review (spec §6.4: local until then). */
export interface Draft {
  body: string;
  label: ThreadLabel | null;
  /** null: no suggested change. */
  suggest: string | null;
}
export const EMPTY: Draft = { body: "", label: null, suggest: null };

/** The comment composer (prototype 467–486). The text lives in `drafts`,
 *  keyed by target, so moving around the page does not lose it. */
export function Composer({ target, lines, drafts, editing, onSubmit, onCancel }: {
  target: Target;
  /** The new-side text of the range, for a suggested change. */
  lines: string[];
  drafts: Map<string, Draft>;
  /** A draft thread being edited: its values start the composer. */
  editing?: ReviewThread | null;
  /** Sends it; resolves to the server's refusal, or null. */
  onSubmit: (d: Draft) => Promise<string | null>;
  onCancel: () => void;
}) {
  const key = targetKey(target);
  const first = editing?.comments[0];
  const [d, setD] = useState<Draft>(() => drafts.get(key) ?? (first ? { body: first.body, label: editing!.label, suggest: first.suggestion?.replacement ?? null } : EMPTY));
  const [preview, setPreview] = useState(false);
  const [asking, setAsking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLTextAreaElement>(null);
  useEffect(() => box.current?.focus(), []);
  const set = (patch: Partial<Draft>) => {
    const next = { ...d, ...patch };
    setD(next);
    drafts.set(key, next);
  };
  const r = target.range;
  const where = !r ? "Comment on this file" : r.start === r.end ? `Line ${r.start}` : `Lines ${r.start}–${r.end}`;
  const cancel = () => {
    drafts.delete(key);
    onCancel();
  };
  const dirty = !!(d.body.trim() || d.suggest !== null);
  const submit = async () => {
    setBusy(true);
    const e = await onSubmit(d);
    setBusy(false);
    setError(e);
    if (!e) drafts.delete(key);
  };
  return (
    <div
      className="rv-composer"
      role="group"
      aria-label={`Comment: ${where}`}
      onKeyDown={(e) => {
        if (e.key !== "Escape") return;
        e.stopPropagation();
        if (dirty) setAsking(true);
        else cancel();
      }}
    >
      <div className="rv-composer-head">
        <span className="rv-muted">{where} ·</span>
        <span role="radiogroup" aria-label="Label" className="rv-chips">
          {LABELS.map(([k, text]) => (
            <button key={text} type="button" role="radio" aria-checked={d.label === k} className={`rv-chip${d.label === k ? " is-on" : ""}`} onClick={() => set({ label: k })}>
              {text}
            </button>
          ))}
        </span>
        <span className="rv-spacer" />
        <button type="button" className="rv-chip" aria-pressed={preview} onClick={() => setPreview((p) => !p)}>{preview ? "Continue editing" : "Preview"}</button>
      </div>
      {preview ? (
        <div className="rv-preview"><Markdown text={d.body || "Nothing to preview"} code={codeBlock} /></div>
      ) : (
        <textarea ref={box} className="rv-textarea" aria-label="Comment" placeholder={r ? `Leave a comment on ${where.toLowerCase()}` : "Comment on this file"} value={d.body} onChange={(e) => set({ body: e.target.value })} />
      )}
      {d.suggest !== null && r?.side === "new" && (
        <div className="rv-suggest">
          <div className="rv-suggest-head">Suggested change · {where.toLowerCase()}</div>
          {lines.map((l, i) => <div key={i} className="rv-suggest-line is-del"><span aria-hidden="true">−</span>{l}</div>)}
          <textarea className="rv-textarea rv-mono" aria-label="Suggested change" value={d.suggest} onChange={(e) => set({ suggest: e.target.value })} />
        </div>
      )}
      {asking ? (
        <div className="rv-row-actions" role="alert">
          <span>Discard this comment?</span>
          <span className="rv-spacer" />
          <Button onClick={() => setAsking(false)}>Keep editing</Button>
          <Button variant="danger" onClick={cancel}>Discard</Button>
        </div>
      ) : (
        <div className="rv-row-actions">
          <span className="rv-muted">{preview ? "Rendered preview · Continue editing to change the text" : "Markdown supported"}</span>
          <span className="rv-spacer" />
          {r?.side === "new" && (
            <Button aria-pressed={d.suggest !== null} onClick={() => set({ suggest: d.suggest === null ? lines.join("\n") : null })}>± Suggest change</Button>
          )}
          <Button onClick={() => (dirty ? setAsking(true) : cancel())}>Cancel</Button>
          <Button variant="primary" disabled={!d.body.trim() || busy} title={d.body.trim() ? undefined : "A comment needs some text"} onClick={submit}>
            {editing ? "Save" : "Add to review"}
          </Button>
        </div>
      )}
      {error && <p className="rv-error" role="alert">{error}</p>}
    </div>
  );
}
