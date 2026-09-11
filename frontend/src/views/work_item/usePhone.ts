import { useEffect, useState } from "react";

/** The README breakpoint (common rules): `max-width: 767px`. Used only for
 *  behaviour a CSS media query can't express by itself — m04's 8-line log
 *  cap needs JS to slice the array, not just to hide/show it. */
export function usePhone(): boolean {
  const query = "(max-width: 767px)";
  const supported = typeof window !== "undefined" && typeof window.matchMedia === "function";
  const [phone, setPhone] = useState(() => (supported ? window.matchMedia(query).matches : false));
  useEffect(() => {
    if (!supported) return;
    const mql = window.matchMedia(query);
    // jsdom test environments sometimes stub `matchMedia` with a bare mock
    // that has no real listener API (theme.test.ts's `vi.stubGlobal`, which
    // can outlive its own file when tests share a worker) — fail quiet
    // rather than crash a render that has nothing to do with the theme.
    if (typeof mql.addEventListener !== "function") return;
    const onChange = () => setPhone(mql.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [supported]);
  return phone;
}
