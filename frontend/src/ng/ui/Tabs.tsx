import { useRef, type KeyboardEvent } from "react";
import "./ui.css";

/** A tab strip: the active tab is underlined, never filled (Decisions §9).
 *  Roving tabindex; ←/→, Home and End move and select. The caller renders
 *  the panel with `id={`${id}-panel`}` and `aria-labelledby` the tab. */
export function Tabs<T extends string>({ label, tabs, value, onChange, id }: { label: string; tabs: { value: T; label: string }[]; value: T; onChange: (v: T) => void; id: string }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const at = Math.max(0, tabs.findIndex((t) => t.value === value));
  const onKey = (e: KeyboardEvent) => {
    const n = tabs.length;
    const i = { ArrowRight: (at + 1) % n, ArrowLeft: (at - 1 + n) % n, Home: 0, End: n - 1 }[e.key];
    if (i === undefined) return;
    e.preventDefault();
    onChange(tabs[i].value);
    refs.current[i]?.focus();
  };
  return (
    <div role="tablist" aria-label={label} className="tabs" onKeyDown={onKey}>
      {tabs.map((t, i) => (
        <button
          key={t.value}
          ref={(el) => void (refs.current[i] = el)}
          id={`${id}-tab-${t.value}`}
          type="button"
          role="tab"
          aria-selected={i === at}
          aria-controls={`${id}-panel`}
          tabIndex={i === at ? 0 : -1}
          className="tab"
          onClick={() => onChange(t.value)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
