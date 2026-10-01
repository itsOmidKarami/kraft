import { useEffect, useRef, useState, type ReactNode } from "react";

/** A value that edits in place (Decisions §12 "Values edit in place"): a button
 *  until clicked or Entered, then a one-line input. Enter or leaving it commits,
 *  Escape cancels. `onCommit` answers a refusal's message, which stays under the
 *  input with the input open and nothing saved, or null once it took. */
export function ValueCell({ label, value, display, onCommit, mono = true, placeholder, bad, muted, changed, readOnly }: {
  /** Names the control for a screen reader: "<label>, <display>. Edit". */
  label: string;
  /** What the input starts with; empty for a value that is not set. */
  value: string;
  /** What the button shows. */
  display: ReactNode;
  onCommit: (text: string) => Promise<string | null> | string | null;
  mono?: boolean;
  placeholder?: string;
  bad?: boolean;
  muted?: boolean;
  /** The value differs from the published one. */
  changed?: boolean;
  readOnly?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(value);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const settled = useRef(false);

  useEffect(() => {
    if (!editing) setText(value);
  }, [value, editing]);
  useEffect(() => {
    if (editing) {
      settled.current = false;
      input.current?.focus();
      input.current?.select();
    }
  }, [editing]);

  const close = (focus: boolean) => {
    setEditing(false);
    setError(null);
    // Hand focus back only if nothing took it meanwhile (a click on the next cell).
    if (focus) requestAnimationFrame(() => { if (document.activeElement === document.body || !document.activeElement) button.current?.focus(); });
  };
  const commit = async () => {
    if (settled.current || busy) return;
    if (text.trim() === value.trim()) return close(true);
    settled.current = true;
    setBusy(true);
    const refused = await onCommit(text.trim());
    setBusy(false);
    if (refused) {
      settled.current = false;
      setError(refused);
      input.current?.focus();
    } else close(true);
  };

  if (!editing)
    return (
      <button
        ref={button}
        type="button"
        disabled={readOnly}
        className={`adr-val${mono ? " is-mono" : ""}${muted ? " is-muted" : ""}${changed ? " is-changed" : ""}${bad ? " is-bad" : ""}`}
        aria-label={readOnly ? undefined : `${label}, ${typeof display === "string" ? display : value || "not set"}. Edit`}
        onClick={() => setEditing(true)}
      >
        {display}
      </button>
    );
  return (
    <span className="adr-edit">
      <input
        ref={input}
        className={`adr-in${mono ? " is-mono" : ""}${error ? " is-bad" : ""}`}
        aria-label={label}
        aria-invalid={error ? true : undefined}
        aria-busy={busy || undefined}
        spellCheck={false}
        value={text}
        placeholder={placeholder}
        onChange={(e) => {
          setText(e.target.value);
          setError(null);
        }}
        onBlur={() => void commit()}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            void commit();
          } else if (e.key === "Escape") {
            // Not the pane's collapse, not the page's.
            e.stopPropagation();
            e.nativeEvent.stopImmediatePropagation();
            settled.current = true;
            close(true);
          }
        }}
      />
      {error && <span className="adr-err" role="alert">{error}</span>}
    </span>
  );
}
