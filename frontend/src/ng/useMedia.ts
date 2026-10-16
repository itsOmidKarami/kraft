import { useEffect, useState } from "react";

const read = (query: string) => typeof matchMedia === "function" && matchMedia(query).matches;

/** Whether a media query matches, live. */
export function useMedia(query: string): boolean {
  const [on, setOn] = useState(() => read(query));
  useEffect(() => {
    if (typeof matchMedia !== "function") return;
    const mq = matchMedia(query);
    const change = () => setOn(mq.matches);
    change();
    mq.addEventListener?.("change", change);
    return () => mq.removeEventListener?.("change", change);
  }, [query]);
  return on;
}
