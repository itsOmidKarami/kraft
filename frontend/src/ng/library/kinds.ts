import { SECTIONS, type Section } from "./types";

const KEY = "kraft.ng.library.kinds";

/** The kinds the person turned off, remembered per browser; nothing hidden when storage is unavailable. */
export function loadHidden(): Set<Section> {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    return new Set(Array.isArray(raw) ? raw.filter((k): k is Section => (SECTIONS as readonly unknown[]).includes(k)) : []);
  } catch {
    return new Set();
  }
}

export function saveHidden(hidden: ReadonlySet<Section>) {
  try {
    localStorage.setItem(KEY, JSON.stringify([...hidden]));
  } catch {
    // Private windows: the filter just isn't remembered.
  }
}
