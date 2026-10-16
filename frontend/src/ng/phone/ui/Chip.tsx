import type { ReactNode } from "react";
import "./ui.css";

/** A filter you press. `pressed` makes it a toggle (aria-pressed); without it the chip opens a sheet, and `set` marks a filter that holds a value. */
export function Chip({ pressed, set, onClick, children }: { pressed?: boolean; set?: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" className={`ph-chip${pressed ? " ph-is-on" : ""}${set ? " ph-is-set" : ""}`} aria-pressed={pressed} onClick={onClick}>
      {children}
    </button>
  );
}
