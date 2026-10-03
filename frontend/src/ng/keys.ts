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
 *  must be false while a send is in flight: a quick second ⌘↵ would send twice. */
export const sendOnModEnter = (send: () => unknown, ready: boolean) => (e: ReactKeyboardEvent) => {
  if (e.key !== "Enter" || !(e.metaKey || e.ctrlKey) || e.shiftKey || e.altKey || e.nativeEvent.isComposing) return;
  e.preventDefault();
  if (ready) void send();
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
