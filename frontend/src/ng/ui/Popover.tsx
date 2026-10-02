import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import "./ui.css";

/** The one floating surface (Menu builds on it). Placed under its anchor
 *  (above it when there is no room below), right-aligned when it would run off the viewport; Escape and a press
 *  outside both it and the anchor close it. */
export function Popover({ anchor, open, onClose, children, role, label }: { anchor: RefObject<HTMLElement | null>; open: boolean; onClose: () => void; children: ReactNode; role?: string; label?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    if (!open || !anchor.current || !ref.current) return setPos(null);
    const a = anchor.current.getBoundingClientRect();
    const w = ref.current.offsetWidth, h = ref.current.offsetHeight;
    // Above the anchor when it would run off the bottom (a pane footer's card).
    const top = a.bottom + 4 + h > window.innerHeight - 8 && a.top - 4 - h >= 8 ? a.top - 4 - h : a.bottom + 4;
    setPos({ top, left: a.left + w > window.innerWidth - 8 ? Math.max(8, a.right - w) : a.left });
  }, [open, anchor]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!ref.current?.contains(t) && !anchor.current?.contains(t)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      onClose();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, onClose, anchor]);

  if (!open) return null;
  // Measured unplaced, but never `visibility: hidden`: a browser refuses focus
  // to a hidden element, and what opens this (Menu, an autoFocus field) moves
  // focus in before the placed frame. That frame comes before the first paint;
  // until then nothing in it takes a pointer.
  return createPortal(
    <div ref={ref} role={role} aria-label={label} className="popover" style={pos ?? { opacity: 0, pointerEvents: "none" }}>
      {children}
    </div>,
    document.body,
  );
}
