import { useId, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { backdropProps, useModal } from "../../useModal";
import "./ui.css";

/** A modal: focus moves in and stays in, Escape or a press on the backdrop
 *  closes it (not while `dirty`), and focus goes back where it was. */
/** `className` lets a page size its own dialog with a class it owns (R59: only ui/ styles `.dialog`). */
export function Dialog({ title, onClose, children, footer, dirty, className }: { title: string; onClose: () => void; children: ReactNode; footer?: ReactNode; dirty?: boolean; className?: string }) {
  const ref = useModal<HTMLDivElement>(onClose);
  const id = useId();
  return createPortal(
    <div className="dialog-backdrop" {...backdropProps(onClose, dirty)}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={id} className={className ? `dialog ${className}` : "dialog"}>
        <h2 id={id} className="dialog-title">{title}</h2>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-footer">{footer}</div>}
      </div>
    </div>,
    document.body,
  );
}
