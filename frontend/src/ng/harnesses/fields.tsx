import { useEffect, useId, useRef, useState } from "react";
import { Combobox } from "../ui/Combobox";

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
  const field = {
    id,
    className: `hn-input${changed ? " is-changed" : ""}`,
    placeholder,
    autoFocus,
    onFocus: () => void (focused.current = true),
    onBlur: () => { focused.current = false; commit(text); },
    onKeyDown: (e: React.KeyboardEvent<HTMLInputElement>) => { if (e.key === "Enter") { e.preventDefault(); commit(text); } },
  };
  const type = (v: string) => {
    setText(v);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => commit(v), PAUSE_MS);
  };
  return (
    <div className="hn-field">
      <label htmlFor={id} className="hn-field-label">{label}</label>
      {suggestions.length > 0 ? (
        // The provider's known models, to pick from; any other may still be typed.
        <Combobox {...field} value={text} choices={suggestions.map((value) => ({ value }))} listLabel="Known models" onChange={type} onPick={commit} />
      ) : (
        <input {...field} spellCheck={false} value={text} onChange={(e) => type(e.target.value)} />
      )}
    </div>
  );
}
