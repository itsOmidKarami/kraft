import type { ReactNode } from "react";
import "./ui.css";

/** A form row whose label is always visible (no placeholder-as-label). The
 *  control goes in `children`; the label element wraps it, so it is named. */
export function Field({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {error ? <span className="field-error" role="alert">{error}</span> : hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}
