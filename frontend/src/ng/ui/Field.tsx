import { cloneElement, isValidElement, useId, type ReactNode } from "react";
import "./ui.css";

/** A form row whose label is always visible (no placeholder-as-label). The
 *  control goes in `children`; the label element wraps it, so it is named. */
export function Field({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: ReactNode; children: ReactNode }) {
  const errId = useId();
  // One control: an error describes it, and marks it invalid unless the control decides that itself
  // (a server failure is an error about the form, not about what was typed).
  const control =
    error && isValidElement<Record<string, unknown>>(children)
      ? cloneElement(children, {
          "aria-invalid": "aria-invalid" in children.props ? children.props["aria-invalid"] : true,
          "aria-describedby": [children.props["aria-describedby"], errId].filter(Boolean).join(" "),
        })
      : children;
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {control}
      {error ? <span id={errId} className="field-error" role="alert">{error}</span> : hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}
