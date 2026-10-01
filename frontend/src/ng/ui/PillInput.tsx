import { useId, useRef, useState, type KeyboardEvent } from "react";
import "./ui.css";

/** A list of short names edited as pills (Decisions §11 Allowed tools): type and
 *  press Enter or comma to add; × or Backspace on an empty input removes one;
 *  Delete or Backspace on a focused pill's × removes it. `added` marks the pills
 *  that are new in the draft (green). The list is announced as it changes. */
export function PillInput({ label, values, added = [], onChange, placeholder = "type and press Enter", empty }: {
  label: string;
  values: string[];
  added?: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  /** Said under an empty list. */
  empty?: string;
}) {
  const [text, setText] = useState("");
  const [said, setSaid] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const id = useId();

  const add = (raw: string) => {
    const v = raw.trim().replace(/,$/, "").trim();
    setText("");
    if (!v || values.includes(v)) return;
    onChange([...values, v]);
    setSaid(`Added ${v}`);
  };
  const remove = (v: string) => {
    onChange(values.filter((x) => x !== v));
    setSaid(`Removed ${v}`);
    input.current?.focus();
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add(text);
    } else if (e.key === "Backspace" && !text && values.length) {
      remove(values[values.length - 1]);
    }
  };
  return (
    <div className="pill-box" role="group" aria-label={label}>
      <ul className="pill-list" role="list">
        {values.map((v) => (
          <li
            key={v}
            className={`pill${added.includes(v) ? " is-added" : ""}`}
            onKeyDown={(e) => {
              if ((e.key === "Delete" || e.key === "Backspace") && (e.target as HTMLElement).tagName === "BUTTON") {
                e.preventDefault();
                remove(v);
              }
            }}
          >
            <span>{v}</span>
            <button type="button" className="pill-x" aria-label={`Remove ${v}`} onClick={() => remove(v)}>×</button>
          </li>
        ))}
        <li className="pill-add">
          <input
            ref={input}
            className="pill-input"
            aria-label={`Add to ${label}`}
            aria-describedby={empty && !values.length ? `${id}-empty` : undefined}
            placeholder={values.length ? "add…" : placeholder}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={onKey}
            onBlur={() => add(text)}
          />
        </li>
      </ul>
      {empty && !values.length && <p id={`${id}-empty`} className="pill-empty">{empty}</p>}
      <span className="pill-live" role="status" aria-live="polite">{said}</span>
    </div>
  );
}
