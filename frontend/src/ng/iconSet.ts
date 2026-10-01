import { useEffect, useState } from "react";
import type { LucideIcon } from "lucide-react";

/** The whole Lucide set, one lazy chunk (brief Decided 12, Kraft-kvsgb):
 *  loaded when the picker opens or a node names an icon outside the static map. */
let set: Record<string, LucideIcon> | null = null;
let loading: Promise<Record<string, LucideIcon>> | null = null;

export function loadIconSet(): Promise<Record<string, LucideIcon>> {
  loading ??= import("./iconSetData").then((m) => (set = m.ICONS));
  return loading;
}

/** The set once loaded; asks for it when `want` is true. */
export function useIconSet(want = true): Record<string, LucideIcon> | null {
  const [s, setS] = useState(set);
  useEffect(() => {
    if (!want || s) return;
    let live = true;
    void loadIconSet().then((x) => live && setS(x));
    return () => {
      live = false;
    };
  }, [want, s]);
  return s;
}
