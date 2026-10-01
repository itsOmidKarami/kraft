import { useEffect, useRef } from "react";

/** Focus the element a frame after it mounts: a Popover stays hidden until it
 *  has measured its place, and a hidden element takes no focus (autoFocus fails). */
export function useFocusSoon<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  useEffect(() => {
    const f = requestAnimationFrame(() => ref.current?.focus());
    return () => cancelAnimationFrame(f);
  }, []);
  return ref;
}
