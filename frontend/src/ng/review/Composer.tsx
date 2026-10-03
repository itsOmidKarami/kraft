import { useEffect, useRef, useState } from "react";
import type { ReviewThread, ThreadLabel } from "../../types";
import { Button } from "../ui/Button";
import { Markdown } from "../ui/Markdown";
import { IconButton } from "../ui/IconButton";
import { X } from "../icons";
import { isMixed, isOneLine, rangeLabel, startSideOf, type LineRange } from "./range";
import { LABELS, codeBlock } from "./Thread";
import { mod, sendOnModEnter } from "../keys";

/** Where a comment goes: a range of a file's lines, or the whole file. */
export interface Target {
  path: string;
  range: LineRange | null;
}
export const targetKey = (t: Target) => (t.range ? `${t.path}|${startSideOf(t.range)}${t.range.start}|${t.range.side}${t.range.end}` : `${t.path}|file`);

/** The composer's text until Add to review (spec §6.4: local until then). */
export interface Draft {
  body: string;
  label: ThreadLabel | null;
  /** null: no suggested change. */
  suggest: string | null;
  /** The range a kept suggestion was written for, once the range moved under it (R10b-09). */
  wrote?: string;
}
export const EMPTY: Draft = { body: "", label: null, suggest: null };

/** The comment composer (prototype 467–486). The text lives in `drafts`,
 *  keyed by target, so moving around the page does not lose it. */
export function Composer({ target, lines, onCollapse, drafts, editing, onSubmit, onCancel }: {
  target: Target;
  /** The new-side text of the range, for a suggested change. */
  lines: string[];
  /** The header's ×: the range back to its last line. Absent, or on one line, no ×. */
  onCollapse?: () => void;
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
  const chips = useRef<(HTMLButtonElement | null)[]>([]);
  useEffect(() => box.current?.focus(), []);
  const set = (patch: Partial<Draft>) => {
    const next = { ...d, ...patch };
    setD(next);
    drafts.set(key, next);
  };
  const r = target.range;
  const where = r ? rangeLabel(r) : "Comment on this file";
  // A range picked across a gap between hunks holds lines the diff doesn't show, so there is nothing to edit them from.
  const gap = !!r && lines.length < r.end - r.start + 1;
  // A suggestion replaces new-side lines, so a range across sides takes none.
  const suggests = r?.side === "new" && !isMixed(r);
  const cancel = () => {
    drafts.delete(key);
    onCancel();
  };
  const dirty = !!(d.body.trim() || d.suggest !== null);
  const ready = !!d.body.trim() && !busy;
  // One ⌘↵ handler, on the wrapper, for both boxes: a second one on a box
  // would send twice before React re-renders `ready`.
  const send = sendOnModEnter(() => submit(), ready);
  const submit = async () => {
    if (!ready) return;
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
        // ⌘↵ or Ctrl+↵ sends it from either box, as every composer does.
        send(e);
        if (e.key !== "Escape") return;
        e.stopPropagation();
        if (dirty) setAsking(true);
        else cancel();
      }}
    >
      <div className="rv-composer-head">
        {r ? (
          <span className="rv-range-name">
            {where}
            {!editing && onCollapse && !isOneLine(r) && <IconButton label="Back to the last line" onClick={onCollapse}><X size={12} aria-hidden /></IconButton>}
          </span>
        ) : (
          <span className="rv-muted">{where}</span>
        )}
        <span className="rv-muted" aria-hidden="true">·</span>
        {/* One tab stop, the checked chip; the arrows move the choice, as the ARIA radio group does (R10b-11). */}
        <span role="radiogroup" aria-label="Label" className="rv-chips" onKeyDown={(e) => {
          const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
          if (!step) return;
          e.preventDefault();
          const at = LABELS.findIndex(([k]) => k === d.label);
          const to = (at + step + LABELS.length) % LABELS.length;
          set({ label: LABELS[to][0] });
          chips.current[to]?.focus();
        }}>
          {LABELS.map(([k, text], i) => (
            <button key={text} ref={(el) => void (chips.current[i] = el)} type="button" role="radio" aria-checked={d.label === k} tabIndex={d.label === k ? 0 : -1} className={`rv-chip${d.label === k ? " is-on" : ""}`} onClick={() => set({ label: k })}>
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
      {d.suggest !== null && d.wrote && (
        // The range moved under it: the typed suggestion is kept, never dropped unsaid (R10b-09).
        <p className="rv-moved" role="status">
          {suggests
            ? `Your suggested change was written for ${d.wrote.toLowerCase()}. Check that it should replace ${where.toLowerCase()}, or remove it.`
            : `Your suggested change, written for ${d.wrote.toLowerCase()}, is set aside: a suggestion replaces new lines only. Move the range back onto the new side to bring it back.`}
        </p>
      )}
      {d.suggest !== null && suggests && (
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
          <span className="rv-muted rv-notes">
            <span>{preview ? "Rendered preview · Continue editing to change the text" : `Markdown supported · ${mod("↵")} ${editing ? "save" : "add to review"}`}</span>
            {/* Ranges have no button of their own: say how to make one, and how to change it. */}
            {r && isOneLine(r) && !editing && !preview && <span>Drag the + or Shift-click to comment on several lines</span>}
            {r && !isOneLine(r) && !editing && !preview && <span>Drag the handles or Shift-click to change the lines</span>}
          </span>
          <span className="rv-spacer" />
          {suggests && gap && <span className="rv-muted">No suggestion across lines the diff doesn't show</span>}
          {suggests && (
            <Button aria-pressed={d.suggest !== null} disabled={gap && d.suggest === null} onClick={() => set({ suggest: d.suggest === null ? lines.join("\n") : null, wrote: undefined })}>± Suggest change</Button>
          )}
          <Button onClick={() => (dirty ? setAsking(true) : cancel())}>Cancel</Button>
          <Button variant="primary" disabled={!ready} title={d.body.trim() ? undefined : "A comment needs some text"} onClick={submit}>
            {editing ? "Save" : "Add to review"}
          </Button>
        </div>
      )}
      {error && <p className="rv-error" role="alert">{error}</p>}
    </div>
  );
}
