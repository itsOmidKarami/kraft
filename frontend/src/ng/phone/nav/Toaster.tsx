import { useToasts } from "../../ui/toastModel";

/** The phone's toaster (W17 brief A.6): the same `kraft:toast` event and the
 *  same stack as the desktop's, above the tab bar. */
export function Toaster() {
  const { toasts, dismiss, bind } = useToasts();
  const view = (tone: "error" | undefined) =>
    toasts.filter((t) => t.tone === tone).map((t) => (
      <div key={t.id} ref={bind(t.id)} role={tone ? "alert" : undefined} className={tone ? "ph-toast ph-toast-error" : "ph-toast"}>
        <span>{t.message}</span>
        {t.action && (
          <button type="button" className="ph-toast-action" onClick={() => { dismiss(t.id); t.action!.run(); }}>
            {t.action.label}
          </button>
        )}
        {tone && (
          <button type="button" className="ph-toast-action" onClick={() => dismiss(t.id)}>Dismiss</button>
        )}
      </div>
    ));
  return (
    <div className="ph-toasts">
      {view("error")}
      <div className="ph-toasts-live" role="status" aria-live="polite">{view(undefined)}</div>
    </div>
  );
}
