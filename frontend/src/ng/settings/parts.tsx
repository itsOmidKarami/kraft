import type { ReactNode } from "react";

/** A titled block of a settings page: the shared frame of Access, Notifications and About. */
export function Block({ id, title, aside, card, pill, hidden, children }: { id: string; title: string; aside?: string; /** A bordered card (AreaAccess): the title at its top left, the aside at its top right. */ card?: boolean | "dashed"; /** The aside as a small outlined tag ("live"). */ pill?: boolean; /** The title is for a screen reader only (a card with no visible heading). */ hidden?: boolean; children: ReactNode }) {
  return (
    <section className={`set-block${card ? " is-card" : ""}${card === "dashed" ? " is-dashed" : ""}`} aria-labelledby={id}>
      <div className="set-block-head">
        <h2 id={id} className={hidden ? "adr-sr" : undefined}>{title}</h2>
        {aside && <span className={pill ? "set-block-pill" : "set-block-aside"}>{aside}</span>}
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
