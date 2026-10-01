import { ChevronLeft } from "lucide-react";
import type { ReactNode } from "react";
import { useBack } from "./trail";

/** The header of every screen but a root (W17 brief A.7): a Back control of at
 *  least 44px that names the parent, an optional identifier, a trailing control. */
export function ScreenHeader({ id, trailing, children }: { id?: ReactNode; trailing?: ReactNode; children?: ReactNode }) {
  const back = useBack();
  return (
    <header className="ph-header">
      <button type="button" className="ph-back" onClick={back.go}>
        <ChevronLeft size={20} aria-hidden="true" />
        {back.label}
      </button>
      {id && <span className="ph-header-id">{id}</span>}
      <span className="ph-spacer" />
      {trailing}
      {children}
    </header>
  );
}

/** A root's title: the 26px page title in place of Back. */
export function RootHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="ph-root-header">
      <h1 className="ph-title">{title}</h1>
      {children}
    </header>
  );
}
