import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Button } from "../../ui/Button";

/** The phone's one overlay (W17 brief A.5). It has exactly three shapes, so a
 *  sheet carries at most one primary action: a confirm (Cancel and one primary),
 *  a choice (option rows) and an edit (one field and one Set). `Frame` is not
 *  exported: a fourth shape is a change to this file, and Sheet.test.tsx pins
 *  the list. */

type State = { phSheet?: string } | null;

/** A sheet is a history entry: Back closes the sheet, not the screen. `id` names
 *  which sheet a screen has open, so a screen can hold several. */
export function useSheet() {
  const navigate = useNavigate();
  const loc = useLocation();
  const openId = (loc.state as State)?.phSheet ?? null;
  const open = useCallback((id: string) => navigate(loc.pathname + loc.search, { state: { phSheet: id } }), [navigate, loc.pathname, loc.search]);
  const close = useCallback(() => navigate(-1), [navigate]);
  // A change that rewrites the screen's own address (a filter) runs once the sheet's entry has been popped, so it lands on the screen and not on the entry the sheet was opened over.
  const after = useRef<(() => void) | null>(null);
  useEffect(() => {
    if (openId === null && after.current) {
      const run = after.current;
      after.current = null;
      run();
    }
  }, [openId]);
  const closeThen = useCallback((fn: () => void) => {
    after.current = fn;
    navigate(-1);
  }, [navigate]);
  /** Leave for another address from inside a sheet: the sheet's entry is replaced, so Back comes to the screen under it. */
  const goTo = useCallback((to: string) => navigate(to, { replace: true }), [navigate]);
  return { openId, is: (id: string) => openId === id, open, close, closeThen, goTo };
}

function Frame({ title, text, onClose, children }: { title: string; text?: string; onClose: () => void; children: ReactNode }) {
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const first = ref.current?.querySelector<HTMLElement>("input, textarea, [data-autofocus], button");
    (first ?? ref.current)?.focus();
    return () => opener?.focus?.();
  }, []);
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
    }
    if (e.key !== "Tab") return;
    const all = [...(ref.current?.querySelectorAll<HTMLElement>("input, textarea, select, button:not(:disabled)") ?? [])];
    if (!all.length) return;
    const at = all.indexOf(document.activeElement as HTMLElement);
    const next = e.shiftKey ? (at <= 0 ? all.length - 1 : at - 1) : at === all.length - 1 ? 0 : at + 1;
    e.preventDefault();
    all[next].focus();
  };
  return (
    <>
      <div className="ph-scrim" onClick={onClose} />
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1} className="ph-sheet" onKeyDown={onKey}>
        <span className="ph-sheet-grip" aria-hidden="true" />
        <h2 id={id} className="ph-sheet-title">{title}</h2>
        {text && <p className="ph-sheet-text">{text}</p>}
        {children}
      </div>
    </>
  );
}

export function ConfirmSheet({ title, text, confirm, onClose, busy, error }: { title: string; text?: string; confirm: { label: string; danger?: boolean; run: () => void }; onClose: () => void; busy?: boolean; error?: string | null }) {
  return (
    <Frame title={title} text={text} onClose={onClose}>
      {error && <p className="ph-sheet-error" role="alert">{error}</p>}
      <div className="ph-sheet-pair">
        <Button className="ph-btn" onClick={onClose}>Cancel</Button>
        <Button className="ph-btn ph-btn-primary" variant={confirm.danger ? "danger" : "primary"} disabled={busy} onClick={confirm.run}>{confirm.label}</Button>
      </div>
    </Frame>
  );
}

export interface Option<T extends string = string> {
  value: T;
  label: string;
  hint?: string;
  danger?: boolean;
}

export function ChoiceSheet<T extends string>({ title, text, options, value, onPick, onClose }: { title: string; text?: string; options: Option<T>[]; value?: T | null; onPick: (v: T) => void; onClose: () => void }) {
  // `value` given (even null): a single choice, announced as radios. Absent: a menu of actions.
  const single = value !== undefined;
  return (
    <Frame title={title} text={text} onClose={onClose}>
      <ul className="ph-sheet-options" role={single ? "radiogroup" : undefined} aria-label={single ? title : undefined}>
        {options.map((o) => (
          <li key={o.value} role="presentation">
            <button
              type="button"
              role={single ? "radio" : undefined}
              aria-checked={single ? value === o.value : undefined}
              className={`ph-sheet-option${o.danger ? " ph-is-danger" : ""}`}
              onClick={() => onPick(o.value)}
            >
              <span className="ph-sheet-check" aria-hidden="true">{value === o.value ? "✓" : ""}</span>
              <span className="ph-sheet-label">{o.label}</span>
              {o.hint && <span className="ph-sheet-hint">{o.hint}</span>}
            </button>
          </li>
        ))}
      </ul>
    </Frame>
  );
}

export function EditSheet({ title, text, initial = "", placeholder, secret, multiline, submitLabel = "Set", error, busy, onSubmit, onClose }: { title: string; text?: string; initial?: string; placeholder?: string; secret?: boolean; multiline?: boolean; submitLabel?: string; error?: string | null; busy?: boolean; onSubmit: (value: string) => void; onClose: () => void }) {
  const [value, setValue] = useState(initial);
  return (
    <Frame title={title} text={text} onClose={onClose}>
      <form className="ph-sheet-edit" onSubmit={(e) => { e.preventDefault(); onSubmit(value); }}>
        {multiline ? (
          <textarea className="ph-input ph-input-area" aria-label={title} value={value} placeholder={placeholder} onChange={(e) => setValue(e.target.value)} />
        ) : (
          <input className="ph-input" aria-label={title} type={secret ? "password" : "text"} value={value} placeholder={placeholder} autoComplete="off" onChange={(e) => setValue(e.target.value)} />
        )}
        <Button className="ph-btn ph-btn-primary" variant="primary" type="submit" disabled={busy}>{submitLabel}</Button>
      </form>
      {error && <p className="ph-sheet-error" role="alert">{error}</p>}
    </Frame>
  );
}
