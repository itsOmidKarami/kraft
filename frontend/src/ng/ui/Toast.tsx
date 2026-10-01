import { useEffect, useRef, useState } from "react";
import "./ui.css";

let seq = 0;

export interface ToastAction {
  label: string;
  run: () => void;
}
type Detail = { message: string; ms?: number; action?: ToastAction };

/** Any component announces a finished action; `Toaster`, mounted once, shows
 *  it. An `action` (the board's Undo) is a button in the toast. */
export function showToast(message: string, opts: number | { ms?: number; action?: ToastAction } = {}): void {
  const { ms, action } = typeof opts === "number" ? { ms: opts, action: undefined } : opts;
  window.dispatchEvent(new CustomEvent<Detail>("kraft:toast", { detail: { message, ms, action } }));
}

/** Top-right on desktop, at most three at once. A toast holding focus stays
 *  until focus leaves it, so its action is never pulled from under a keyboard. */
export function Toaster() {
  const [toasts, setToasts] = useState<{ id: number; message: string; action?: ToastAction }[]>([]);
  const els = useRef(new Map<number, HTMLElement>());
  useEffect(() => {
    const on = (e: Event) => {
      const { message, ms = 2600, action } = (e as CustomEvent<Detail>).detail;
      const id = ++seq;
      setToasts((cur) => [...cur, { id, message, action }]);
      const drop = () => {
        if (els.current.get(id)?.contains(document.activeElement)) return void setTimeout(drop, 500);
        setToasts((cur) => cur.filter((t) => t.id !== id));
      };
      setTimeout(drop, ms);
    };
    window.addEventListener("kraft:toast", on);
    return () => window.removeEventListener("kraft:toast", on);
  }, []);
  const dismiss = (id: number) => setToasts((cur) => cur.filter((t) => t.id !== id));
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.slice(-3).map((t) => (
        <div key={t.id} className="toast" ref={(el) => void (el ? els.current.set(t.id, el) : els.current.delete(t.id))}>
          {t.message}
          {t.action && (
            <button type="button" className="toast-action" onClick={() => { dismiss(t.id); t.action!.run(); }}>
              {t.action.label}
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
