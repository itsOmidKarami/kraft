import type { ReactNode } from "react";
import "./ui.css";

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>;
}
