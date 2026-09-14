import { useRef, type ReactNode } from "react";

/** The one shape every composer variant (steer, steer & retry, reject,
 *  answer, escalate/reply, budget) shares (README shared primitives:
 *  "textarea 76px min, 13.5px; primary submit + Cancel; 11px footnote"). */
export function Composer({
  title,
  explanation,
  value,
  onChange,
  placeholder,
  quoted,
  footnote,
  submitLabel,
  onSubmit,
  onCancel,
  busy,
  disabled,
  error,
}: {
  /** `Steer verify` (spec §3) — every composer opens under a header line. */
  title?: ReactNode;
  /** `· the note leads attempt 2's system prompt`, appended to the header. */
  explanation?: ReactNode;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  /** answer (20): the question quoted above the textarea. */
  quoted?: string;
  footnote?: ReactNode;
  submitLabel: string;
  onSubmit: () => void;
  onCancel: () => void;
  busy: boolean;
  disabled?: boolean;
  error?: string | null;
}) {
  // Where the composer was opened from, read on its first render — before
  // autoFocus moves focus into the textarea (W6.4, W6.10).
  const origin = useRef<{ el: Element | null; top: number } | null>(null);
  origin.current ??= { el: document.activeElement, top: document.querySelector("main")?.scrollTop ?? 0 };

  // Cancel puts the page back: the scroll position of `main` and focus on the
  // trigger. The trigger usually re-mounts when the composer closes, so it is
  // found again by its text when the original node is gone.
  const cancel = () => {
    const { el, top } = origin.current!;
    const label = el instanceof HTMLElement ? el.textContent : null;
    onCancel();
    requestAnimationFrame(() => {
      const main = document.querySelector("main");
      if (main) main.scrollTop = top;
      const target =
        el instanceof HTMLElement && el.isConnected
          ? el
          : [...document.querySelectorAll<HTMLElement>("button, a[href]")].find((b) => label && b.textContent === label);
      target?.focus({ preventScroll: true });
    });
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      if (!busy && !disabled) onSubmit();
    } else if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      cancel();
    }
  };

  return (
    <div className="composer">
      {title && (
        <p className="composer-head">
          {title}
          {explanation && <span className="composer-explain"> · {explanation}</span>}
        </p>
      )}
      {quoted && <p className="composer-quoted">{quoted}</p>}
      <textarea
        className="input composer-input"
        aria-label="composer message"
        aria-keyshortcuts="Meta+Enter Control+Enter Escape"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onKeyDown={onKeyDown}
        autoFocus
      />
      {footnote && <p className="field-hint composer-footnote">{footnote}</p>}
      <div className="gate-actions">
        <button className="btn btn-primary" disabled={busy || disabled} onClick={onSubmit}>
          {submitLabel}
        </button>
        <button className="btn btn-ghost" disabled={busy} onClick={cancel}>
          Cancel
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}
