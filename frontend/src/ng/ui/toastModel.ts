import { useCallback, useEffect, useRef, useState } from "react";

export interface ToastAction {
  label: string;
  run: () => void;
}
export type ToastTone = "error";
export interface ToastDetail {
  message: string;
  ms?: number;
  action?: ToastAction;
  tone?: ToastTone;
}
export type ToastRow = ToastDetail & { id: number };

/** How long a toast stays, desktop and phone alike. An error stays until it is dismissed (or for `ms`, when a caller gives it). */
export const TOAST_MS = 4000;
/** How many show at once; the oldest goes first. */
export const MAX_TOASTS = 3;

let seq = 0;

/** The toasts both trees show, from the `kraft:toast` event `showToast` sends. A
 *  toast holding focus stays until focus leaves it, so its action is never pulled
 *  from under a keyboard. `bind` gives each toast's element to that check. */
export function useToasts() {
  const [toasts, setToasts] = useState<ToastRow[]>([]);
  const els = useRef(new Map<number, HTMLElement>());
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  useEffect(() => {
    const on = (e: Event) => {
      const d = (e as CustomEvent<ToastDetail>).detail;
      const id = ++seq;
      setToasts((cur) => [...cur, { ...d, id }].slice(-MAX_TOASTS));
      const ms = d.ms ?? (d.tone === "error" ? undefined : TOAST_MS);
      if (ms === undefined) return;
      const drop = () => {
        if (els.current.get(id)?.contains(document.activeElement)) return void timers.current.add(setTimeout(drop, 500));
        setToasts((cur) => cur.filter((t) => t.id !== id));
      };
      timers.current.add(setTimeout(drop, ms));
    };
    window.addEventListener("kraft:toast", on);
    const pending = timers.current;
    return () => {
      window.removeEventListener("kraft:toast", on);
      pending.forEach(clearTimeout);
    };
  }, []);
  const dismiss = useCallback((id: number) => setToasts((cur) => cur.filter((t) => t.id !== id)), []);
  const bind = (id: number) => (el: HTMLElement | null) => void (el ? els.current.set(id, el) : els.current.delete(id));
  return { toasts, dismiss, bind };
}
