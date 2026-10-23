import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent as RPointerEvent } from "react";

/** The list's width: 250 as it was, never under 200 (a name and its count), never over 480. The canvas keeps
 *  `CANVAS` of the page: the component pane's 300px minimum and a canvas to draw in beside it. */
export const LIST = { MIN: 200, MAX: 480, DEFAULT: 250, STEP: 16, CANVAS: 480 } as const;
const KEY = "kraft.ng.library.list";

const load = (key: string, fallback: number) => {
  try {
    return Number(localStorage.getItem(key)) || fallback;
  } catch {
    return fallback;
  }
};
const save = (key: string, w: number) => {
  try {
    localStorage.setItem(key, String(w));
  } catch {
    // Private windows: the width just isn't remembered.
  }
};

export type ListSize = ReturnType<typeof useListWidth>;

/** The library list's width, dragged or keyed by its right edge, remembered per browser, held under the page's room.
 *  `page` is the page's width (0 until measured), for what the list leaves the canvas. A double click puts it back to
 *  the default. `key` and `fallback` let another page's list (the review's file tree) keep its own. */
export function useListWidth(page: number, key: string = KEY, fallback: number = LIST.DEFAULT) {
  const [raw, setRaw] = useState(() => load(key, fallback));
  const max = Math.max(LIST.MIN, Math.min(LIST.MAX, (page || Infinity) - LIST.CANVAS));
  const clamp = (w: number) => Math.round(Math.max(LIST.MIN, Math.min(max, w)));
  const width = clamp(raw);
  const set = (w: number) => {
    const c = clamp(w);
    setRaw(c);
    save(key, c);
  };
  // The drag in flight, so leaving the page mid-drag lets go of the window.
  const stop = useRef<(() => void) | null>(null);
  useEffect(() => () => stop.current?.(), []);
  const onPointerDown = (e: RPointerEvent) => {
    e.preventDefault();
    stop.current?.();
    const x0 = e.clientX, w0 = width;
    let last = w0;
    const move = (ev: PointerEvent) => {
      last = clamp(w0 + ev.clientX - x0);
      setRaw(last);
    };
    const end = (save_: boolean) => () => {
      stop.current?.();
      // A click that never moved keeps what was stored; a cancelled drag keeps where it was.
      if (save_ && last !== w0) save(key, last);
    };
    const up = end(true), cancel = end(false);
    stop.current = () => {
      stop.current = null;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", cancel);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", cancel);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
    // The handle is the list's right edge: → widens, ← narrows.
    const next = { ArrowRight: width + LIST.STEP, ArrowLeft: width - LIST.STEP, Home: LIST.MIN, End: max }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    e.stopPropagation();
    set(next);
  };
  const handle = {
    role: "separator", "aria-orientation": "vertical", "aria-label": "Resize the list",
    "aria-valuenow": width, "aria-valuemin": LIST.MIN, "aria-valuemax": max,
    tabIndex: 0, onPointerDown, onKeyDown, onDoubleClick: () => set(fallback),
  } as const;
  return { width, handle };
}
