import { useEffect, useState } from "react";

export interface ToastPayload {
  message: string;
  /** phosphor-icons name, e.g. "Check" — resolved by the host, kept as a
   *  string here so this file has no icon-library import of its own. */
  icon?: string;
}

let seq = 0;

/** Fire-and-forget: any component calls this after an action succeeds
 *  (06 "Feedback" — "Approved — chain continues", etc.); `ToastHost`
 *  (mounted once in `App.tsx`) is the only listener. */
export function showToast(message: string, icon?: string): void {
  window.dispatchEvent(new CustomEvent<ToastPayload>("kraft:toast", { detail: { message, icon } }));
}

interface Entry extends ToastPayload {
  id: number;
}

/** Stacked bottom, 2.6s each (README motion table has no exception for
 *  toasts, but 06 "Feedback" pins the duration explicitly). */
export function ToastHost() {
  const [entries, setEntries] = useState<Entry[]>([]);

  useEffect(() => {
    const onToast = (e: Event) => {
      const detail = (e as CustomEvent<ToastPayload>).detail;
      const id = ++seq;
      setEntries((cur) => [...cur, { ...detail, id }]);
      setTimeout(() => setEntries((cur) => cur.filter((t) => t.id !== id)), 2600);
    };
    window.addEventListener("kraft:toast", onToast);
    return () => window.removeEventListener("kraft:toast", onToast);
  }, []);

  if (entries.length === 0) return null;
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {entries.map((t) => (
        <div key={t.id} className="toast">
          {t.message}
        </div>
      ))}
    </div>
  );
}
