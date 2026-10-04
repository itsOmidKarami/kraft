import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import "./ui.css";

/** How long a pointer or the keyboard rests on a button before its tooltip shows. */
export const TIP_DELAY_MS = 400;

/** What a button that shows no text spreads onto itself: its name for a screen reader and for the tooltip,
 *  a short verb phrase ("Collapse all files"). `data-tip` is what the layer reads and what the test
 *  setup checks every icon-only button for. */
export const tip = (label: string, text = label) => ({ "aria-label": label, "data-tip": text }) as const;

const HOST = "[data-tip]";

/** The one tooltip, mounted once per app. It shows the `data-tip` of the button under the pointer or holding
 *  keyboard focus after TIP_DELAY_MS, just under that button (above it when there is no room) so it never
 *  covers it, and goes on leave, blur, press, scroll or Escape. One layer rather than a wrapper per button:
 *  a diff has a button on every line. The button keeps its own aria-label, so a screen reader hears it once. */
export function Tooltip() {
  const [on, setOn] = useState<{ label: string; rect: DOMRect } | null>(null);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let host: Element | null = null;
    const hide = () => {
      clearTimeout(timer);
      host = null;
      setOn(null);
    };
    const arm = (el: Element) => {
      if (el === host) return;
      hide();
      host = el;
      timer = setTimeout(() => setOn({ label: el.getAttribute("data-tip") ?? "", rect: el.getBoundingClientRect() }), TIP_DELAY_MS);
    };
    const hostOf = (t: EventTarget | null) => (t instanceof Element ? t.closest(HOST) : null);
    const over = (e: MouseEvent) => { const h = hostOf(e.target); if (h) arm(h); };
    const out = (e: MouseEvent) => { if (host && hostOf(e.relatedTarget) !== host) hide(); };
    // Keyboard focus only: a click's focus is the click's business, and it hides the tip.
    const focus = (e: FocusEvent) => { const h = hostOf(e.target); if (h?.matches(":focus-visible")) arm(h); };
    const key = (e: KeyboardEvent) => e.key === "Escape" && hide();
    document.addEventListener("mouseover", over);
    document.addEventListener("mouseout", out);
    document.addEventListener("focusin", focus);
    document.addEventListener("focusout", hide);
    document.addEventListener("pointerdown", hide, true);
    document.addEventListener("keydown", key);
    window.addEventListener("scroll", hide, true);
    return () => {
      hide();
      document.removeEventListener("mouseover", over);
      document.removeEventListener("mouseout", out);
      document.removeEventListener("focusin", focus);
      document.removeEventListener("focusout", hide);
      document.removeEventListener("pointerdown", hide, true);
      document.removeEventListener("keydown", key);
      window.removeEventListener("scroll", hide, true);
    };
  }, []);
  return on ? <Bubble {...on} /> : null;
}

function Bubble({ label, rect }: { label: string; rect: DOMRect }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth, h = el.offsetHeight;
    const above = rect.bottom + 6 + h > window.innerHeight - 8 && rect.top - 6 - h >= 8;
    const left = Math.min(Math.max(8, rect.left + rect.width / 2 - w / 2), Math.max(8, window.innerWidth - w - 8));
    setPos({ top: above ? rect.top - 6 - h : rect.bottom + 6, left });
  }, [rect, label]);
  return createPortal(
    <div ref={ref} role="tooltip" className="tip" style={{ top: pos?.top ?? 0, left: pos?.left ?? 0, visibility: pos ? "visible" : "hidden" }}>{label}</div>,
    document.body,
  );
}
