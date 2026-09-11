import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, Check } from "@phosphor-icons/react";
import { Link } from "react-router-dom";

/**
 * Settings (design 5a–5e). Every page edits versioned YAML through the API
 * (`02` §4.7 revised), so each one ends in the same save row: what file is
 * written, and what re-runs when it lands.
 */

/** Load-once-then-edit, the shape every page here needs. */
export function useResource<T>(load: () => Promise<T>) {
  const [value, setValue] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(() => {
    return load()
      .then((v) => {
        setValue(v);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
    // `load` is redefined every render by design — the caller closes over its own
    // state — so it is deliberately not a dependency.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // `reload` now returns a promise (so callers can await it); wrap it here
  // because `useEffect` requires `void | Destructor` — passing the promise
  // straight through would make React treat it as a cleanup function. Do
  // not "simplify" this back to `useEffect(reload, [reload])`.
  useEffect(() => {
    void reload();
  }, [reload]);
  return { value, setValue, error, setError, reload };
}

export function SaveRow({
  onSave,
  onDiscard,
  hint,
  busy,
  dirty,
  message,
}: {
  onSave: () => void;
  onDiscard: () => void;
  hint: string;
  busy?: boolean;
  dirty?: boolean;
  message?: string | null;
}) {
  return (
    <div className="save-row">
      <button className="btn btn-primary" disabled={busy || !dirty} onClick={onSave}>
        <Check size={14} />
        Save
      </button>
      <button className="btn btn-ghost" disabled={busy || !dirty} onClick={onDiscard}>
        Discard
      </button>
      <span className="save-hint">{message ?? hint}</span>
    </div>
  );
}

export function PageHead({ title, note, action }: { title: string; note: string; action?: React.ReactNode }) {
  return (
    <div className="settings-head">
      <h2>{title}</h2>
      <span className="settings-note">{note}</span>
      {action}
    </div>
  );
}

const PHONE_QUERY = "(max-width: 767px)"; // README breakpoint (UI v2 · 00 common)

/** True at phone width, live across a resize — not a one-time read, so
 *  rotating a device or resizing a dev-tools panel doesn't strand the page
 *  between the list and detail layout. */
export function usePhone(): boolean {
  const [phone, setPhone] = useState(() => window.matchMedia(PHONE_QUERY).matches);
  useEffect(() => {
    const mql = window.matchMedia(PHONE_QUERY);
    const onChange = () => setPhone(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return phone;
}

/** m10–m14's phone top bar: `← <parent>` · title · subtitle · one primary
 *  action (Save, or +). `back` is the parent's own label ("Settings" from a
 *  top-level page like Repos; "Repos" from a repo's own detail page) — the
 *  caller names it because only the caller knows which level it's leaving. */
export function PhoneHeader({
  back,
  backTo,
  title,
  subtitle,
  action,
}: {
  back: string;
  backTo: string;
  title: string;
  subtitle?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="phone-header">
      <Link to={backTo} className="phone-header-back">
        <ArrowLeft size={16} />
        {back}
      </Link>
      <div className="phone-header-title">
        <span className="phone-header-h1">{title}</span>
        {subtitle && <span className="phone-header-sub">{subtitle}</span>}
      </div>
      {action}
    </div>
  );
}
