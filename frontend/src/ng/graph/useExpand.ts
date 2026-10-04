import { useEffect, useRef, useState } from "react";

/** Where the open/close of a task's frame is. `mounted`: the frame is in the page; `layout`: the canvas is
 *  laid out for it open (the frame grows from the box, the rest moves aside); `content`: its rows show;
 *  `glide`: something is still moving, so the camera waits to refit. */
export type Phase = { mounted: boolean; layout: boolean; content: boolean; glide: boolean };

/** Open: the box becomes the frame while everything else makes room, then the rows fade in. Close: the rows
 *  fade, then everything moves back and the frame shrinks into the box and fades. Times are the prototype's
 *  (420ms moves; the rows leave in 100ms; the frame is gone by 700ms). With reduced motion nothing moves:
 *  the frame is there or not, and CSS crossfades it. A frame open on load is open at once. */
export function useExpand(open: boolean, reduced: boolean): Phase {
  const [phase, set] = useState<Phase>({ mounted: open, layout: open, content: open, glide: false });
  const was = useRef(open);
  // Read live, not a dependency: a change of preference mid-move would re-run the effect and drop the timers that end the glide.
  const calm = useRef(reduced);
  calm.current = reduced;
  useEffect(() => {
    if (was.current === open) return;
    was.current = open;
    if (calm.current) return set({ mounted: open, layout: open, content: open, glide: false });
    const timers: number[] = [];
    const at = (ms: number, fn: () => void) => void timers.push(window.setTimeout(fn, ms));
    let frames = 0;
    if (open) {
      set({ mounted: true, layout: false, content: false, glide: true });
      // Two frames on, so the box is painted before it moves.
      frames = requestAnimationFrame(() => (frames = requestAnimationFrame(() => set((p) => ({ ...p, layout: true, content: true })))));
      at(540, () => set((p) => ({ ...p, glide: false })));
    } else {
      set((p) => ({ ...p, content: false, glide: true }));
      at(100, () => set((p) => ({ ...p, layout: false })));
      at(720, () => set({ mounted: false, layout: false, content: false, glide: false }));
    }
    return () => {
      cancelAnimationFrame(frames);
      timers.forEach(clearTimeout);
    };
  }, [open]);
  return phase;
}

/** Whether the person asked for less motion, live. */
export function useReducedMotion() {
  const query = "(prefers-reduced-motion: reduce)";
  const [on, setOn] = useState(() => typeof matchMedia === "function" && matchMedia(query).matches);
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(query);
    const change = () => setOn(mq.matches);
    change();
    mq.addEventListener?.("change", change);
    return () => mq.removeEventListener?.("change", change);
  }, []);
  return on;
}

/** `value`, held back while `hold` is true: the camera measures the world after the move, not during it. */
export function useSettled<T>(value: T, hold: boolean): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    if (!hold) setSettled(value);
  }, [value, hold]);
  return settled;
}
