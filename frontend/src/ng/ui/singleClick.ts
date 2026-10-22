import { useCallback, useEffect, useRef, type MouseEvent } from "react";

/** Long enough for a quick double-click's second press, short enough that a single click still reads as instant. */
export const DOUBLE_CLICK_MS = 220;

/** For an element whose double-click opens what its click selects: the click waits out a second press, so a
 *  double-click opens without first flashing the pane the single click shows. A click from the keys (detail 0),
 *  or on an element with no double-click (`waits` false), acts at once; a slower double-click still opens, after
 *  the pane. A press elsewhere or a key while it waits drops it: what the person did next wins. */
export function useSingleClick() {
  const drop = useRef(() => {});
  useEffect(() => () => drop.current(), []);
  return useCallback((e: MouseEvent, act: () => void, waits = true) => {
    drop.current();
    if (!waits || e.detail === 0) return act();
    if (e.detail > 1) return;
    const el = e.currentTarget;
    const away = (ev: Event) => (ev.type === "keydown" || !el.contains(ev.target as Node)) && stop();
    const stop = () => {
      window.clearTimeout(timer);
      window.removeEventListener("pointerdown", away, true);
      window.removeEventListener("keydown", away, true);
      drop.current = () => {};
    };
    const timer = window.setTimeout(() => (stop(), act()), DOUBLE_CLICK_MS);
    window.addEventListener("pointerdown", away, true);
    window.addEventListener("keydown", away, true);
    drop.current = stop;
  }, []);
}
