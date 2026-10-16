/** "rail" is the stored word for unpinned, kept so a stored choice survives. */
export type SidebarMode = "pinned" | "rail";

export const SIDEBAR_KEY = "kraft.sidebar.v2";

/** The stored choice, or null: nothing stored, or storage unreadable. */
export function readSidebar(): SidebarMode | null {
  try {
    const v = localStorage.getItem(SIDEBAR_KEY);
    return v === "pinned" || v === "rail" ? v : null;
  } catch {
    return null;
  }
}

/** Pinned at every width until the reader chooses otherwise. */
export const currentSidebar = (): SidebarMode => readSidebar() ?? "pinned";

/** At or below this width a list, a canvas and a pane do not fit beside a 210px sidebar. */
export const NARROW_MAX = 1279;

let panes = 0;
const listeners = new Set<() => void>();

/** What the layout uses: the stored choice when there is one, and otherwise the
 *  sidebar steps back to its rail while a page has a pane open on a narrow screen.
 *  A person who chose either way is never second-guessed. */
export function effectiveSidebar(): SidebarMode {
  const stored = readSidebar();
  if (stored) return stored;
  return panes > 0 && window.innerWidth <= NARROW_MAX ? "rail" : "pinned";
}

function sync(): void {
  document.documentElement.dataset.sidebar = effectiveSidebar();
  listeners.forEach((l) => l());
}

export function subscribeSidebar(l: () => void): () => void {
  listeners.add(l);
  return () => void listeners.delete(l);
}

/** A page with a pane open calls this; the sidebar returns when the pane closes. */
export function holdPane(): () => void {
  panes += 1;
  sync();
  window.addEventListener("resize", sync);
  return () => {
    panes -= 1;
    if (panes === 0) window.removeEventListener("resize", sync);
    sync();
  };
}

/** Stores the choice and puts it on <html> in the same step. */
export function writeSidebar(mode: SidebarMode): void {
  try {
    localStorage.setItem(SIDEBAR_KEY, mode);
  } catch {
    /* private mode: the choice lasts until reload */
  }
  document.documentElement.dataset.sidebar = mode;
  listeners.forEach((l) => l());
}

/** Called by boot before its first await, so the first paint has the layout the
 *  reader chose and a reload never paints the other one and corrects it. */
export function applyInitialSidebar(): void {
  document.documentElement.dataset.sidebar = currentSidebar();
}
