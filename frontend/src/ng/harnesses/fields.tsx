import { useEffect, useId, useRef, useState } from "react";

export const PAUSE_MS = 800;

/** A text field that commits on Enter, on blur and 800 ms after typing stops
 *  (the Chains pause rule); it follows the server's value while not focused.
 *  `changed` draws it amber until published. An empty text is not committed
 *  unless `clearable`: then it clears the value. */
export function ModelField({ label, value, suggestions = [], changed, clearable, placeholder, onCommit, autoFocus }: {
  label: string;
  clearable?: boolean;
  placeholder?: string;
  value: string;
  suggestions?: string[];
  changed?: boolean;
  onCommit: (v: string) => void;
  autoFocus?: boolean;
}) {
  const [text, setText] = useState(value);
  const focused = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const last = useRef(value);
  const id = useId();
  useEffect(() => {
    last.current = value;
    if (!focused.current) setText(value);
  }, [value]);
  useEffect(() => () => clearTimeout(timer.current), []);
  const commit = (v: string) => {
    clearTimeout(timer.current);
    const next = v.trim();
    if ((next || clearable) && next !== last.current) {
      last.current = next;
      onCommit(next);
    }
  };
  return (
    <div className="hn-field">
      <label htmlFor={id} className="hn-field-label">{label}</label>
      <input
        id={id}
        className={`hn-input${changed ? " is-changed" : ""}`}
        spellCheck={false}
        placeholder={placeholder}
        autoFocus={autoFocus}
        list={suggestions.length ? `${id}-s` : undefined}
        value={text}
        onFocus={() => void (focused.current = true)}
        onBlur={() => { focused.current = false; commit(text); }}
        onChange={(e) => {
          setText(e.target.value);
          clearTimeout(timer.current);
          const v = e.target.value;
          timer.current = setTimeout(() => commit(v), PAUSE_MS);
        }}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(text); } }}
      />
      {suggestions.length > 0 && <datalist id={`${id}-s`}>{suggestions.map((s) => <option key={s} value={s} />)}</datalist>}
    </div>
  );
}
