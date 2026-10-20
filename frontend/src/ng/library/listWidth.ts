import { useState, type KeyboardEvent, type PointerEvent as RPointerEvent, type RefObject } from "react";

/** The list's width: 250 as it was, never under 200 (a name and its count), never over 480 or the page's
 *  width less 360 (the canvas keeps room). */
export const LIST = { MIN: 200, MAX: 480, DEFAULT: 250, STEP: 16, CANVAS: 360 } as const;
const KEY = "kraft.ng.library.list";

const load = () => {
  try {
    return Number(localStorage.getItem(KEY)) || LIST.DEFAULT;
  } catch {
    return LIST.DEFAULT;
  }
};
const save = (w: number) => {
  try {
    localStorage.setItem(KEY, String(w));
  } catch {
    // Private windows: the width just isn't remembered.
  }
};

/** The library list's width, dragged or keyed by its right edge, remembered per browser, held under the page's room.
 *  `page` is the list's container, for what its width leaves the canvas. A double click puts it back to the default. */
export function useListWidth(page: RefObject<HTMLElement | null>) {
  const [raw, setRaw] = useState(load);
  const max = () => Math.max(LIST.MIN, Math.min(LIST.MAX, (page.current?.clientWidth || Infinity) - LIST.CANVAS));
  const clamp = (w: number) => Math.round(Math.max(LIST.MIN, Math.min(max(), w)));
  const width = clamp(raw);
  const set = (w: number) => {
    const c = clamp(w);
    setRaw(c);
    save(c);
    return c;
  };
  const onPointerDown = (e: RPointerEvent) => {
    e.preventDefault();
    const x0 = e.clientX, w0 = width;
    const move = (ev: PointerEvent) => setRaw(clamp(w0 + ev.clientX - x0));
    const up = (ev: PointerEvent) => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      set(w0 + ev.clientX - x0);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };
  const onKeyDown = (e: KeyboardEvent) => {
    // The handle is the list's right edge: → widens, ← narrows.
    const next = { ArrowRight: width + LIST.STEP, ArrowLeft: width - LIST.STEP, Home: LIST.MIN, End: max() }[e.key];
    if (next === undefined) return;
    e.preventDefault();
    set(next);
  };
  const handle = {
    role: "separator", "aria-orientation": "vertical", "aria-label": "Resize the list",
    "aria-valuenow": width, "aria-valuemin": LIST.MIN, "aria-valuemax": max(),
    tabIndex: 0, onPointerDown, onKeyDown, onDoubleClick: () => void set(LIST.DEFAULT),
  } as const;
  return { width, handle };
}
