import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import "./ui.css";

/** The one floating surface (Menu builds on it). Placed under its anchor,
 *  right-aligned when it would run off the viewport; Escape and a press
 *  outside both it and the anchor close it. */
export function Popover({ anchor, open, onClose, children, role, label }: { anchor: RefObject<HTMLElement | null>; open: boolean; onClose: () => void; children: ReactNode; role?: string; label?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  useLayoutEffect(() => {
    if (!open || !anchor.current || !ref.current) return setPos(null);
    const a = anchor.current.getBoundingClientRect();
    const w = ref.current.offsetWidth;
    setPos({ top: a.bottom + 4, left: a.left + w > window.innerWidth - 8 ? Math.max(8, a.right - w) : a.left });
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
  return createPortal(
    <div ref={ref} role={role} aria-label={label} className="popover" style={pos ?? { visibility: "hidden" }}>
      {children}
    </div>,
    document.body,
  );
}
