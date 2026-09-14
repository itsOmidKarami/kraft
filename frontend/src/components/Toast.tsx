import { useEffect, useState } from "react";

export interface ToastPayload {
  message: string;
  /** phosphor-icons name, e.g. "Check" — resolved by the host, kept as a
   *  string here so this file has no icon-library import of its own. */
  icon?: string;
  /** One action on the toast, e.g. "Undo" after an archive (W4.9). */
  action?: { label: string; run: () => unknown };
  /** How long it stays, in ms; 2600 when unset. */
  ms?: number;
}

let seq = 0;

/** Fire-and-forget: any component calls this after an action succeeds
 *  (06 "Feedback" — "Approved — chain continues", etc.); `ToastHost`
 *  (mounted once in `App.tsx`) is the only listener. */
export function showToast(
  message: string,
  icon?: string,
  opts: Pick<ToastPayload, "action" | "ms"> = {},
): void {
  window.dispatchEvent(new CustomEvent<ToastPayload>("kraft:toast", { detail: { message, icon, ...opts } }));
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
      setTimeout(() => setEntries((cur) => cur.filter((t) => t.id !== id)), detail.ms ?? 2600);
    };
    window.addEventListener("kraft:toast", onToast);
    return () => window.removeEventListener("kraft:toast", onToast);
  }, []);

  if (entries.length === 0) return null;
  return (
    <div className="toast-stack" role="status" aria-live="polite">
      {/* At most three at once (W6.1); older ones still time out on their own. */}
      {entries.slice(-3).map((t) => (
        <div key={t.id} className="toast">
          {t.message}
          {t.action && (
            <>
              {" · "}
              <button
                type="button"
                className="toast-action"
                onClick={() => {
                  t.action!.run();
                  setEntries((cur) => cur.filter((x) => x.id !== t.id));
                }}
              >
                {t.action.label}
              </button>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
