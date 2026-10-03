import { useId, useRef, useState, type KeyboardEvent } from "react";
import { Combobox, notListed, unlisted, type Choice } from "./Combobox";
import "./ui.css";
import { tip } from "./Tooltip";

/** A list of short names edited as pills (Decisions §11 Allowed tools): type and
 *  press Enter or comma to add; × or Backspace on an empty input removes one;
 *  Delete or Backspace on a focused pill's × removes it. `added` marks the pills
 *  that are new in the draft (green). The list is announced as it changes. */
export function PillInput({ label, values, added = [], onChange, placeholder = "type and press Enter", empty, choices, noun = "value" }: {
  label: string;
  values: string[];
  added?: string[];
  onChange: (next: string[]) => void;
  placeholder?: string;
  /** Said under an empty list. */
  empty?: string;
  /** The closed set a pill comes from: listed as you type, and nothing else is added. */
  choices?: Choice[];
  /** What one value is, for the refusal ("grant"). */
  noun?: string;
}) {
  const [text, setText] = useState("");
  const [said, setSaid] = useState("");
  const [refused, setRefused] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();

  const add = (raw: string) => {
    const v = raw.trim().replace(/,$/, "").trim();
    const stray = choices ? notListed(unlisted(v, choices), noun) : null;
    if (stray) return setRefused(stray);
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
            <button type="button" className="pill-x" {...tip(`Remove ${v}`)} onClick={() => remove(v)}>×</button>
          </li>
        ))}
        <li className="pill-add">
          {choices ? (
            <Combobox
              ref={input}
              className="pill-input"
              aria-label={`Add to ${label}`}
              aria-describedby={empty && !values.length ? `${id}-empty` : undefined}
              placeholder={values.length ? "add…" : placeholder}
              value={text}
              choices={choices.filter((c) => !values.includes(c.value))}
              closed
              noun={noun}
              listLabel={label}
              invalid={!!refused}
              onChange={(t) => { setText(t); setRefused(null); }}
              onPick={add}
              onKeyDown={onKey}
              onBlur={() => add(text)}
            />
          ) : (
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
          )}
        </li>
      </ul>
      {refused && <p className="pill-err" role="alert">{refused}</p>}
      {empty && !values.length && <p id={`${id}-empty`} className="pill-empty">{empty}</p>}
      <span className="pill-live" role="status" aria-live="polite">{said}</span>
    </div>
  );
}
