import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

export const HeaderActionsHost = createContext<HTMLElement | null>(null);

/** A page's own header buttons: rendered where the page puts it, shown at the
 *  right of the shell's header. Outside a shell it shows nothing. */
export function HeaderActions({ children }: { children: ReactNode }) {
  const host = useContext(HeaderActionsHost);
  return host ? createPortal(children, host) : null;
}
