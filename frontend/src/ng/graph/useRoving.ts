import { useEffect, useRef, useState } from "react";

/** Roving tabindex over a canvas's stops: one Tab stop, which is the selected
 *  stop until the arrows move it; `go` moves and focuses. */
export function useRoving(selected: string | undefined, first: string | undefined) {
  const [active, setActive] = useState<string>();
  const els = useRef(new Map<string, HTMLElement>());
  // A new selection takes the Tab stop back.
  useEffect(() => setActive(undefined), [selected]);
  const current = active && els.current.has(active) ? active : (selected ?? first);
  return {
    active: current,
    ref: (key: string) => (el: HTMLElement | null) => void (el ? els.current.set(key, el) : els.current.delete(key)),
    tabIndex: (key: string) => (key === current ? 0 : -1),
    go: (key: string | undefined) => {
      if (!key) return;
      setActive(key);
      els.current.get(key)?.focus();
    },
  };
}
