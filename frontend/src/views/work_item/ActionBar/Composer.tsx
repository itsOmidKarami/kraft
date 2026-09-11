import type { ReactNode } from "react";

/** The one shape every composer variant (steer, steer & retry, reject,
 *  answer, escalate/reply, budget) shares (README shared primitives:
 *  "textarea 76px min, 13.5px; primary submit + Cancel; 11px footnote"). */
export function Composer({
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
  return (
    <div className="composer">
      {quoted && <p className="composer-quoted">{quoted}</p>}
      <textarea
        className="input composer-input"
        aria-label="composer message"
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        autoFocus
      />
      {footnote && <p className="field-hint composer-footnote">{footnote}</p>}
      <div className="gate-actions">
        <button className="btn btn-primary" disabled={busy || disabled} onClick={onSubmit}>
          {submitLabel}
        </button>
        <button className="btn btn-ghost" disabled={busy} onClick={onCancel}>
          Cancel
        </button>
      </div>
      {error && <p className="form-error">{error}</p>}
    </div>
  );
}
