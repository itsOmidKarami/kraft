import { useCallback, useEffect, useState } from "react";
import { Check } from "@phosphor-icons/react";

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
