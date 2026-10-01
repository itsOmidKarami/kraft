import { useEffect, useState } from "react";
import * as api from "../../api";
import type { LibraryComponent } from "../../types";

let cache: Promise<LibraryComponent[]> | null = null;

/** The published library's components, for "From the library…" and "extend a
 *  node from the library" (Decisions §9). Read once per page load. */
export function useLibrary(): LibraryComponent[] | "loading" | "failed" {
  const [list, setList] = useState<LibraryComponent[] | "loading" | "failed">("loading");
  useEffect(() => {
    cache ??= api.getLibrary().then((l) => l.components);
    let live = true;
    cache.then((c) => live && setList(c)).catch(() => {
      cache = null;
      if (live) setList("failed");
    });
    return () => {
      live = false;
    };
  }, []);
  return list;
}

export const resetLibrary = () => void (cache = null);
