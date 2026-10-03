import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import "./ui.css";

const ITEMS = '[role="menuitem"]:not(:disabled), [role="menuitemradio"]:not(:disabled), [role="menuitemcheckbox"]:not(:disabled)';
const FOCUSABLE = 'input:not(:disabled), textarea:not(:disabled), select:not(:disabled), button:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])';

/** The first thing to focus in `root`: its first live menu item, else its first field or button. */
export const firstFocusable = (root: HTMLElement | null): HTMLElement | null =>
  root?.querySelector<HTMLElement>(ITEMS) ?? root?.querySelector<HTMLElement>(FOCUSABLE) ?? null;

/** The one floating surface (Menu builds on it). Placed under its anchor
 *  (above it when there is no room below), right-aligned when it would run off the viewport; Escape and a press
 *  outside both it and the anchor close it.
 *
 *  It is reachable from the keyboard however it was opened:
 *  - once placed, focus moves to its first menu item, field or button, unless
 *    what opened it already moved focus in, or `focusIn` is false (a toggle
 *    that opens on hover or focus and moves in only on a key);
 *  - ↑/↓, Home and End move between its menu items, and Tab leaves a menu as Escape does;
 *  - Tab and Shift+Tab cycle inside a dialog (a confirm card, a small form), as
 *    in a dialog: closing it on Tab would lose what was typed, and leaving it
 *    open would send focus to the top of the page;
 *  - Escape hands focus back to what had it when it opened, unless `onClose` moved it elsewhere. */
export function Popover({ anchor, open, onClose, children, role, label, focusIn = true }: { anchor: RefObject<HTMLElement | null>; open: boolean; onClose: () => void; children: ReactNode; role?: string; label?: string; focusIn?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const opener = useRef<HTMLElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const placed = pos !== null;

  useLayoutEffect(() => {
    if (!open || !anchor.current || !ref.current) return setPos(null);
    const a = anchor.current.getBoundingClientRect();
    const w = ref.current.offsetWidth, h = ref.current.offsetHeight;
    // Above the anchor when it would run off the bottom (a pane footer's card).
    const top = a.bottom + 4 + h > window.innerHeight - 8 && a.top - 4 - h >= 8 ? a.top - 4 - h : a.bottom + 4;
    setPos({ top, left: a.left + w > window.innerWidth - 8 ? Math.max(8, a.right - w) : a.left });
  }, [open, anchor]);

  // What had focus as it opened: where Escape hands it back. Read before Menu
  // moves focus in (its effect runs after this); a field inside that took it
  // with autoFocus leaves only the anchor to go back to.
  useLayoutEffect(() => {
    const at = document.activeElement;
    if (open) opener.current = at instanceof HTMLElement && at !== document.body && !ref.current?.contains(at) ? at : null;
  }, [open]);

  useEffect(() => {
    if (!open || !placed || !focusIn) return;
    // A frame later: a child that focuses its own field (useFocusSoon) has had its turn by then.
    const f = requestAnimationFrame(() => {
      if (!ref.current || ref.current.contains(document.activeElement)) return;
      firstFocusable(ref.current)?.focus();
    });
    return () => cancelAnimationFrame(f);
  }, [open, placed, focusIn]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !anchor.current?.contains(t)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      closeBack();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose, anchor]);

  // Close, and hand focus back to what had it when this opened, unless `onClose` already moved it.
  function closeBack() {
    onClose();
    // Still inside: nobody took focus back, and it would fall to the page as this unmounts.
    if (ref.current?.contains(document.activeElement)) (opener.current?.isConnected ? opener.current : anchor.current)?.focus();
  }

  // ↑/↓, Home and End over the menu items; a list that handles its own keys prevents the default first.
  // Tab leaves a menu as Escape does: portalled to the end of the page, the
  // next stop after its last item would be the top of the page, with the menu still open.
  const onItemKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.defaultPrevented) return;
    if (e.key === "Tab" && role === "menu") {
      e.preventDefault();
      return closeBack();
    }
    if (e.key === "Tab" && role === "dialog") {
      const all = [...e.currentTarget.querySelectorAll<HTMLElement>(FOCUSABLE)];
      const at = document.activeElement;
      const to = e.shiftKey ? (at === all[0] ? all.at(-1) : null) : at === all.at(-1) ? all[0] : null;
      if (to || !all.length) e.preventDefault();
      to?.focus();
      return;
    }
    const items = [...e.currentTarget.querySelectorAll<HTMLElement>(ITEMS)];
    const n = items.length;
    const at = items.indexOf(document.activeElement as HTMLElement);
    const next = { ArrowDown: (at + 1) % n, ArrowUp: at < 0 ? n - 1 : (at - 1 + n) % n, Home: 0, End: n - 1 }[e.key];
    if (next === undefined || !n) return;
    e.preventDefault();
    items[next].focus();
  };

  if (!open) return null;
  // Measured unplaced, but never `visibility: hidden`: a browser refuses focus
  // to a hidden element, and what opens this (Menu, an autoFocus field) moves
  // focus in before the placed frame. That frame comes before the first paint;
  // until then nothing in it takes a pointer.
  return createPortal(
    <div ref={ref} role={role} aria-label={label} className="popover" style={pos ?? { opacity: 0, pointerEvents: "none" }} onKeyDown={onItemKey}>
      {children}
    </div>,
    document.body,
  );
}
