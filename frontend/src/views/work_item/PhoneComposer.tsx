import type { ReactNode } from "react";

/** A composer as a full-screen page on phone (m08), rather than an inline
 *  expand inside the bar (desktop). */
export function PhoneComposer({
  title,
  context,
  onCancel,
  children,
}: {
  title: string;
  context: string;
  onCancel: () => void;
  children: ReactNode;
}) {
  return (
    <div className="phone-composer" data-testid="phone-composer">
      <div className="phone-composer-head">
        <button className="btn btn-ghost" onClick={onCancel}>
          Cancel
        </button>
        <span className="phone-composer-title">{title}</span>
      </div>
      <p className="phone-composer-context">{context}</p>
      <div className="phone-composer-body">{children}</div>
    </div>
  );
}
