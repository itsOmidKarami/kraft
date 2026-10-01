import { useEffect, useState } from "react";

/** An element's width and height, kept current. */
export function useBox() {
  const [el, setEl] = useState<HTMLElement | null>(null);
  const [box, setBox] = useState({ w: 0, h: 600 });
  useEffect(() => {
    if (!el) return;
    const measure = () => setBox({ w: el.clientWidth, h: el.clientHeight || 600 });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [el]);
  return [setEl, box.w, box.h] as const;
}
