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

/** Stores the choice and puts it on <html> in the same step. */
export function writeSidebar(mode: SidebarMode): void {
  try {
    localStorage.setItem(SIDEBAR_KEY, mode);
  } catch {
    /* private mode: the choice lasts until reload */
  }
  document.documentElement.dataset.sidebar = mode;
}

/** Called by boot before its first await, so the first paint has the layout the
 *  reader chose and a reload never paints the other one and corrects it. */
export function applyInitialSidebar(): void {
  document.documentElement.dataset.sidebar = currentSidebar();
}
