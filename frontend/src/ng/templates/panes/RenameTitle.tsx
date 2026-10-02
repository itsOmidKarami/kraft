import { useEffect, useId, useRef, useState } from "react";
import type { Ref } from "../cards/refs";
import { idError } from "../ids";

/** Rename in place (Decisions §9 Rename): the pane's title becomes its id
 *  field with the text selected, the way a Config row edits its value. Enter
 *  or a blur renames, Esc (or a blur with it blank) keeps the id; the references the rename also
 *  updates are named under it. */
export function RenameTitle({ what, id, taken, refs, refused, chain, onGo, onCancel }: {
  what: string;
  id: string;
  taken: string[];
  refs: Ref[];
  /** The server's refusal of the last try. */
  refused?: string | null;
  /** Renaming the chain itself: the repos defaulting to it follow it. */
  chain?: boolean;
  onGo: (id: string) => void;
  onCancel: () => void;
}) {
  const [text, setText] = useState(id);
  const [sent, setSent] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  // Esc ends it: the blur its unmounting may fire renames nothing.
  const kept = useRef(false);
  const hint = useId();
  useEffect(() => {
    input.current?.focus();
    input.current?.select();
  }, []);
  const err = !text ? "Type an id, or Esc to keep it." : idError(text, taken.filter((x) => x !== id)) ?? (refused && sent === text ? refused : null);
  const keep = () => {
    kept.current = true;
    onCancel();
  };
  const save = (blur = false) => {
    if (kept.current) return;
    // Nothing typed, or left blank by a blur: no op, so no undo step (a Config row's rule).
    if (text === id || (blur && !text)) return keep();
    // Refused here, or already sent: the field stays with its reason.
    if (err || sent === text) return;
    setSent(text);
    onGo(text);
  };
  const note = refs.length
    ? `Renaming also updates ${refs.length} reference${refs.length === 1 ? "" : "s"}: ${refs.map((r) => r.path).join(", ")}`
    : chain ? "Repos defaulting to it follow the new id when you publish." : "Nothing else refers to it.";
  return (
    <>
      <input
        ref={input}
        className={`pane-title-edit${err ? " is-bad" : ""}`}
        aria-label={`Rename ${what}`}
        aria-invalid={!!err}
        aria-describedby={hint}
        value={text}
        spellCheck={false}
        onChange={(e) => setText(e.target.value.trim())}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); save(); }
          // Esc keeps the id; the pane stays open (its own Esc collapses it).
          if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); keep(); }
        }}
        onBlur={() => save(true)}
      />
      <p id={hint} className={`pane-title-note${err ? " is-bad" : ""}`} role={err ? "alert" : undefined}>{err ?? note}</p>
    </>
  );
}
