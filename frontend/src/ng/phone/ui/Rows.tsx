import type { ReactNode } from "react";
import "./ui.css";

/** The whole row is the target of a switch (W17 brief 0.3), announced as a switch. */
export function SwitchRow({ label, hint, on, onChange, disabled }: { label: string; hint?: string; on: boolean; onChange: (on: boolean) => void; disabled?: boolean }) {
  return (
    <button type="button" role="switch" aria-checked={on} disabled={disabled} className="ph-row ph-switch-row" onClick={() => onChange(!on)}>
      <span className="ph-row-text">
        <span className="ph-row-label">{label}</span>
        {hint && <span className="ph-row-hint">{hint}</span>}
      </span>
      <span className={`ph-switch${on ? " ph-is-on" : ""}`} aria-hidden="true"><span className="ph-switch-knob" /></span>
    </button>
  );
}

/** A small-caps heading over a bordered block. */
export function Block({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="ph-block" aria-label={title}>
      <div className="ph-block-head"><h2>{title}</h2>{aside && <span>{aside}</span>}</div>
      {children}
    </section>
  );
}

/** Key and value rows (the prototype's two-column grid). */
export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  if (!rows.length) return null;
  return (
    <dl className="ph-facts">
      {rows.map(([k, v]) => (
        <div key={k} className="ph-fact"><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

/** Tabs of one screen: 44px, an underline on the open one. */
export function TabStrip<T extends string>({ tabs, value, onChange, label }: { tabs: { id: T; label: string }[]; value: T; onChange: (t: T) => void; label: string }) {
  return (
    <div className="ph-tabstrip" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={value === t.id} className={`ph-tabstrip-tab${value === t.id ? " ph-is-on" : ""}`} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** The bottom bar of a screen: a pair of 48px buttons, or one. */
export function ActionBar({ children }: { children: ReactNode }) {
  return <div className="ph-actionbar">{children}</div>;
}
