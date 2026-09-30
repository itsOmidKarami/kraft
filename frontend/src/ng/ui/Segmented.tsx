import { useRef, type KeyboardEvent } from "react";
import "./ui.css";

export interface Option<T extends string> {
  value: T;
  label: string;
}

const STEP: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };

/** A single choice among a few, as a radio group: one tab stop, arrows move
 *  the choice (and change it, as native radios do). */
export function Segmented<T extends string>({ label, options, value, onChange, disabled }: { label: string; options: Option<T>[]; value: T; onChange: (v: T) => void; disabled?: boolean }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const at = Math.max(0, options.findIndex((o) => o.value === value));
  const onKey = (e: KeyboardEvent) => {
    const i = e.key === "Home" ? 0 : e.key === "End" ? options.length - 1 : e.key in STEP ? (at + STEP[e.key] + options.length) % options.length : -1;
    if (i < 0) return;
    e.preventDefault();
    onChange(options[i].value);
    refs.current[i]?.focus();
  };
  return (
    <div role="radiogroup" aria-label={label} className="segmented" onKeyDown={onKey}>
      {options.map((o, i) => (
        <button
          key={o.value}
          ref={(el) => void (refs.current[i] = el)}
          type="button"
          role="radio"
          aria-checked={i === at}
          tabIndex={i === at ? 0 : -1}
          disabled={disabled}
          className="segmented-option"
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
