import { useEffect, useState, type KeyboardEvent, type PointerEvent as RPointerEvent } from "react";

export const PANE = { MIN: 300, RESERVE: 460, DEFAULT: 380, STEP: 16 } as const;

/** Decisions §9 Panes: 300px up to the canvas width minus 460, never under 300. */
export const clampPane = (w: number, canvas: number) => Math.max(PANE.MIN, Math.min(canvas - PANE.RESERVE, w));
const key = (page: string) => `kraft.ng.pane.${page}`;

function load(page: string, fallback: number) {
  try {
    return Number(localStorage.getItem(key(page))) || fallback;
  } catch {
    return fallback;
  }
}
function save(page: string, w: number) {
  try {
    localStorage.setItem(key(page), String(w));
  } catch {
    // Private windows: the width just isn't remembered.
  }
}

/** True under 1024px, live: the pane overlays the canvas there (R7). */
export function useOverlay() {
  const query = "(max-width: 1023px)";
  const [on, setOn] = useState(() => typeof matchMedia === "function" && matchMedia(query).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(query);
    const change = () => setOn(mq.matches);
    change();
    mq.addEventListener("change", change);
    return () => mq.removeEventListener("change", change);
  }, []);
  return on;
}

/** An element's width, kept current. */
export function useWidth() {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [w, setW] = useState(0);
  useEffect(() => {
    if (!el) return;
    setW(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, w] as const;
}

/** The side pane's width for one page: dragged or keyed by its inner edge,
 *  remembered under kraft.ng.pane.<page>, re-clamped to the canvas. Under 1024
 *  the pane overlays at min(380, canvas − 40) with no handle. */
export function useResizable(page: string, canvas: number, fallback: number = PANE.DEFAULT) {
  const overlay = useOverlay();
  const [raw, setRaw] = useState(() => load(page, fallback));
  const max = Math.max(PANE.MIN, canvas - PANE.RESERVE);
  const width = overlay ? Math.min(PANE.DEFAULT, canvas - 40) : clampPane(raw, canvas);
  const set = (w: number) => {
    const c = clampPane(w, canvas);
    setRaw(c);
    return c;
  };
  const onPointerDown = (e: RPointerEvent) => {
    e.preventDefault();
    e.stopPropagation();
    const x0 = e.clientX, w0 = width;
    let last = w0;
    const move = (ev: PointerEvent) => void (last = set(w0 + x0 - ev.clientX));
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      save(page, last);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    // The handle is the pane's left edge: ← widens, → narrows.
    const next = { ArrowLeft: width + PANE.STEP, ArrowRight: width - PANE.STEP, Home: PANE.MIN, End: max }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    e.stopPropagation();
    save(page, set(next));
  };
  const handle = overlay
    ? undefined
    : { role: "separator", "aria-orientation": "vertical", "aria-label": "Resize pane", "aria-valuenow": width, "aria-valuemin": PANE.MIN, "aria-valuemax": max, tabIndex: 0, onPointerDown, onKeyDown } as const;
  return { width, overlay, handle };
}
