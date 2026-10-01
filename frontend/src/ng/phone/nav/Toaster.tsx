import { useEffect, useRef, useState } from "react";
import type { ToastAction } from "../../ui/Toast";

type Detail = { message: string; ms?: number; action?: ToastAction };

export const TOAST_MS = 3200;

/** The phone's toaster (W17 brief A.6): the same `kraft:toast` event as
 *  `ng/ui` `showToast`, so shared code that toasts reaches it, one toast at a
 *  time, above the tab bar. A toast holding focus stays until focus leaves. */
export function Toaster() {
  const [toast, setToast] = useState<(Detail & { id: number }) | null>(null);
  const el = useRef<HTMLDivElement>(null);
  const seq = useRef(0);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const on = (e: Event) => {
      const d = (e as CustomEvent<Detail>).detail;
      const id = ++seq.current;
      setToast({ ...d, id });
      clearTimeout(timer);
      const drop = () => {
        if (el.current?.contains(document.activeElement)) return void (timer = setTimeout(drop, 500));
        setToast((cur) => (cur?.id === id ? null : cur));
      };
      timer = setTimeout(drop, d.ms ?? TOAST_MS);
    };
    window.addEventListener("kraft:toast", on);
    return () => {
      window.removeEventListener("kraft:toast", on);
      clearTimeout(timer);
    };
  }, []);
  return (
    <div className="ph-toasts" role="status" aria-live="polite">
      {toast && (
        <div ref={el} className="ph-toast">
          <span>{toast.message}</span>
          {toast.action && (
            <button type="button" className="ph-toast-action" onClick={() => { setToast(null); toast.action!.run(); }}>
              {toast.action.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
