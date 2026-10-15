import type { ReactNode } from "react";
import { SECTIONS, type Section } from "./sections";

/** The title every Policy section opens with: its name, a line on what it is for, and an action when the section has one. */
export function SectionHead({ id, children, action }: { id: Section; children: ReactNode; action?: ReactNode }) {
  return (
    <header className="pol-head">
      <h2 className="pol-title">{SECTIONS.find((s) => s.id === id)!.label}</h2>
      <p className="pol-intro">{children}</p>
      {action}
    </header>
  );
}
