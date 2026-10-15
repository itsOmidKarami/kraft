import { MAX_TOASTS, useToasts, type ToastAction, type ToastTone } from "./toastModel";
import "./ui.css";

export type { ToastAction } from "./toastModel";

/** Any component announces a finished action; `Toaster`, mounted once, shows
 *  it. An `action` (the board's Undo) is a button in the toast. `tone: "error"`
 *  is for a failure: it is announced at once and stays until dismissed. */
export function showToast(message: string, opts: number | { ms?: number; action?: ToastAction; tone?: ToastTone } = {}): void {
  const { ms, action, tone } = typeof opts === "number" ? { ms: opts, action: undefined, tone: undefined } : opts;
  window.dispatchEvent(new CustomEvent("kraft:toast", { detail: { message, ms, action, tone } }));
}

/** Bottom-right on desktop (the phone has its own stack above its nav), at most `MAX_TOASTS` at once. */
export function Toaster() {
  const { toasts, dismiss, bind } = useToasts();
  const view = (tone: "error" | undefined) =>
    toasts.filter((t) => t.tone === tone).map((t) => (
      <div key={t.id} role={tone ? "alert" : undefined} className={tone ? "toast toast-error" : "toast"} ref={bind(t.id)}>
        <span>{t.message}</span>
        {t.action && (
          <button type="button" className="toast-action" onClick={() => { dismiss(t.id); t.action!.run(); }}>
            {t.action.label}
          </button>
        )}
        {tone && (
          <button type="button" className="toast-action" onClick={() => dismiss(t.id)}>Dismiss</button>
        )}
      </div>
    ));
  return (
    <div className="toasts" data-max={MAX_TOASTS}>
      {view("error")}
      <div className="toasts-live" role="status" aria-live="polite">{view(undefined)}</div>
    </div>
  );
}
