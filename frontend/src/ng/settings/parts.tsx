import type { ReactNode } from "react";

/** A titled block of a settings page: the shared frame of Access, Notifications and About. */
export function Block({ id, title, aside, children }: { id: string; title: string; aside?: string; children: ReactNode }) {
  return (
    <section className="set-block" aria-labelledby={id}>
      <div className="set-block-head">
        <h2 id={id}>{title}</h2>
        {aside && <span className="set-block-aside">{aside}</span>}
      </div>
      {children}
    </section>
  );
}

/** One labelled row inside a block: label, the control, then its hint. */
export function SetRow({ label, hint, error, children }: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode }) {
  return (
    <div className="set-line-row">
      <span className="set-row-label">{label}</span>
      <div className="set-row-body">{children}</div>
      {error ? <span className="set-error" role="alert">{error}</span> : hint ? <span className="set-hint">{hint}</span> : null}
    </div>
  );
}
