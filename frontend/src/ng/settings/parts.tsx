import type { ReactNode } from "react";

/** The one saved-state cue of a settings page, for the header's actions: what it says is in savedWords. */
export function SavedCue({ word }: { word: string }) {
  return <span className="saved-note">{word}</span>;
}

/** A titled block of a settings page: the shared frame of Access, Notifications and About. */
export function Block({ id, title, aside, action, card, hidden, children }: { id: string; title: string; aside?: string; /** A control at the head's right end. */ action?: ReactNode; /** A bordered card (AreaAccess): the title at its top left, the aside at its top right. */ card?: boolean | "dashed"; /** The title is for a screen reader only (a card with no visible heading). */ hidden?: boolean; children: ReactNode }) {
  return (
    <section className={`set-block${card ? " is-card" : ""}${card === "dashed" ? " is-dashed" : ""}`} aria-labelledby={id}>
      <div className="set-block-head">
        <h2 id={id} className={hidden ? "adr-sr" : undefined}>{title}</h2>
        {aside && <span className="set-block-aside">{aside}</span>}
        {action && <span className="set-block-action">{action}</span>}
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
