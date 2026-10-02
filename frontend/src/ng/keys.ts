import type { KeyboardEvent as ReactKeyboardEvent } from "react";

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
