import { useEffect, useRef, useState } from "react";
import type { ReviewThread, ThreadLabel } from "../../types";
import { Pencil } from "lucide-react";
import { Button } from "../ui/Button";
import { Markdown } from "../ui/Markdown";
import { Menu } from "../ui/Menu";
import { isMixed, isOneLine, startSideOf, type LineRange } from "./range";
import type { Anchor, Side } from "./rows";
import { LABELS, codeBlock, lineRef, rangeName } from "./Thread";
import { sendOnModEnter } from "../keys";

/** Where a comment goes: a range of a file's lines, or the whole file. */
export interface Target {
  path: string;
  range: LineRange | null;
}
export const targetKey = (t: Target) => (t.range ? `${t.path}|${startSideOf(t.range)}${t.range.start}|${t.range.side}${t.range.end}` : `${t.path}|file`);

/** A line the range could start on instead, with its text. */
export interface StartOption {
  at: Anchor;
  text: string;
}

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
export function Composer({ target, lines, starts, onStart, drafts, editing, onSubmit, onCancel }: {
  target: Target;
  /** The new-side text of the range, for a suggested change. */
  lines: string[];
  /** The lines above the range's end it could start on instead (the header's pencil); none hides it. */
  starts?: StartOption[];
  onStart?: (a: Anchor) => void;
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
  const where = r ? rangeName(r) : "Comment on this file";
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
          <span className="rv-muted rv-range-name">
            Comment on {isOneLine(r) ? "line" : "lines"} <LineChip side={startSideOf(r)} line={r.start} />
            {!isOneLine(r) && <> to <LineChip side={r.side} line={r.end} /></>}
          </span>
        ) : (
          <span className="rv-muted">{where}</span>
        )}
        {r && !editing && onStart && starts && starts.length > 1 && (
          <Menu
            label="Change the start line"
            heading="Start line"
            trigger={<Pencil size={12} aria-hidden />}
            items={starts.map((o) => ({
              label: lineRef(o.at.side, o.at.line),
              hint: o.text.trim().slice(0, 48) || " ",
              checked: o.at.side === startSideOf(r) && o.at.line === r.start,
              onSelect: () => onStart(o.at),
            }))}
          />
        )}
        <span className="rv-muted" aria-hidden="true">·</span>
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
            <span>{preview ? "Rendered preview · Continue editing to change the text" : `Markdown supported · ⌘↵ ${editing ? "save" : "add to review"}`}</span>
            {/* Ranges have no button of their own: say how to make one where one line was picked. */}
            {r && isOneLine(r) && !editing && !preview && <span>Drag the + or Shift-click to comment on several lines</span>}
          </span>
          <span className="rv-spacer" />
          {suggests && gap && <span className="rv-muted">No suggestion across lines the diff doesn't show</span>}
          {suggests && (
            <Button aria-pressed={d.suggest !== null} disabled={gap && d.suggest === null} onClick={() => set({ suggest: d.suggest === null ? lines.join("\n") : null })}>± Suggest change</Button>
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

/** A line number in the composer's header, coloured by its side. */
const LineChip = ({ side, line }: { side: Side; line: number }) => <span className={`rv-line-chip is-${side}`}>{lineRef(side, line)}</span>;
