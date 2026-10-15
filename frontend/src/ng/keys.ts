import { useEffect, useSyncExternalStore } from "react";
import type { KeyboardEvent as ReactKeyboardEvent } from "react";

/** Whether this keyboard's modifier is ⌘: a Mac, or an iPad or iPhone with a keyboard. */
export const onMac = () => {
  if (typeof navigator === "undefined") return false;
  const platform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform || navigator.platform || navigator.userAgent;
  return /mac|iphone|ipad|ipod/i.test(platform);
};

/** A shortcut as this platform prints it: "⌘K" on a Mac, "Ctrl+K" off one. The
 *  handlers take either modifier everywhere; only the words differ. */
export const mod = (key: string) => (onMac() ? `⌘${key}` : `Ctrl+${key}`);

/** A key that types into a field belongs to the field, not to a page shortcut. */
export const isTextField = (t: EventTarget | null) =>
  t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName));

/** ⌘↵, or Ctrl+↵ off a Mac, in a multi-line field sends it, as the button
 *  beside it would; a plain ↵ keeps its newline. `ready` is that button's own
 *  enabled state, so the keys never send what the button would refuse, and it
 *  must be false while a send is in flight: a quick second ⌘↵ would send twice.
 *  `refused` runs instead when it is false, so the keys never fail silently. */
export const sendOnModEnter = (send: () => unknown, ready: boolean, refused?: () => void) => (e: ReactKeyboardEvent) => {
  if (e.key !== "Enter" || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || e.nativeEvent.isComposing) return;
  e.preventDefault();
  if (ready) void send();
  else refused?.();
};

/** Whether a key came from a field that holds typed text: Escape there must not
 *  close what holds the field and drop the text (R11b-02). A select, a checkbox
 *  or an empty box holds nothing to lose. */
export const holdsText = (t: EventTarget | null) => {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return (t.textContent ?? "") !== "";
  if (t instanceof HTMLTextAreaElement) return t.value !== "";
  return t instanceof HTMLInputElement && !["checkbox", "radio", "button", "submit", "reset", "range", "color", "file"].includes(t.type) && t.value !== "";
};

/** An Escape the page may act on: not one that ends an input method's composition, which
 *  belongs to the field (Safari sends it with `isComposing` set). */
export const isEscape = (e: ReactKeyboardEvent) => e.key === "Escape" && !e.nativeEvent.isComposing;

/** One line of the shortcut help: the keys, and what they do. */
export interface ShortcutRow {
  keys: string[];
  does: string;
}

/** The rows every page answers to, as the shell binds them. */
export const globalShortcuts = (): ShortcutRow[] => [
  { keys: [mod("K")], does: "Search" },
  { keys: ["⇧N"], does: "New work item" },
  { keys: [mod("\\")], does: "Pin or collapse the sidebar" },
  { keys: [mod("↵")], does: "Send, in a field that has a send button" },
  { keys: ["?"], does: "Show this list" },
];

/** Pages add their own rows while mounted, and the one help dialog lists them
 *  after the global ones. Rows of a page are named by `page` in the dialog. */
const pages = new Map<symbol, { page: string; rows: ShortcutRow[] }>();
const listeners = new Set<() => void>();
let snapshot: { page: string; rows: ShortcutRow[] }[] = [];
const publish = () => {
  snapshot = [...pages.values()];
  listeners.forEach((l) => l());
};

/** Registers `rows` for as long as the calling page is mounted. Pass a stable `rows`. */
export function useShortcutRows(page: string, rows: ShortcutRow[]) {
  useEffect(() => {
    const id = Symbol(page);
    pages.set(id, { page, rows });
    publish();
    return () => void (pages.delete(id), publish());
  }, [page, rows]);
}

/** The rows pages have registered right now. */
export const useRegisteredShortcuts = () => useSyncExternalStore((cb) => (listeners.add(cb), () => void listeners.delete(cb)), () => snapshot);

/** Whether a key press is a bare printable press, one a page shortcut may take: not in a field, no modifier. */
export const isBareKey = (e: KeyboardEvent) => !e.defaultPrevented && !e.metaKey && !e.ctrlKey && !e.altKey && !e.isComposing && !isTextField(e.target);

/** Shortcuts stand down while a dialog is open: it owns the keyboard. */
export const dialogOpen = () => !!document.querySelector('[role="dialog"], [role="alertdialog"]');

/** Opens the shortcut help from anywhere (the board's "?" button). */
export const openShortcutHelp = () => window.dispatchEvent(new Event("kraft:shortcuts"));
