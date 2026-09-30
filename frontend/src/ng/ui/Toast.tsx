import { useEffect, useState } from "react";
import "./ui.css";

let seq = 0;

/** Any component announces a finished action; `Toaster`, mounted once, shows it. */
export function showToast(message: string, ms = 2600): void {
  window.dispatchEvent(new CustomEvent("kraft:toast", { detail: { message, ms } }));
}

/** Top-right on desktop, at most three at once. */
export function Toaster() {
  const [toasts, setToasts] = useState<{ id: number; message: string }[]>([]);
  useEffect(() => {
    const on = (e: Event) => {
      const { message, ms = 2600 } = (e as CustomEvent<{ message: string; ms?: number }>).detail;
      const id = ++seq;
      setToasts((cur) => [...cur, { id, message }]);
      setTimeout(() => setToasts((cur) => cur.filter((t) => t.id !== id)), ms);
    };
    window.addEventListener("kraft:toast", on);
    return () => window.removeEventListener("kraft:toast", on);
  }, []);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.slice(-3).map((t) => (
        <div key={t.id} className="toast">{t.message}</div>
      ))}
    </div>
  );
}
