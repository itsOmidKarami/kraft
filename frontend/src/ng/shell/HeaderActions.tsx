import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

export const HeaderActionsHost = createContext<HTMLElement | null>(null);
export const HeaderTailHost = createContext<HTMLElement | null>(null);

/** A page's own header buttons: rendered where the page puts it, shown at the
 *  right of the shell's header. Outside a shell it shows nothing. */
export function HeaderActions({ children }: { children: ReactNode }) {
  const host = useContext(HeaderActionsHost);
  return host ? createPortal(children, host) : null;
}

/** What a page adds right after the crumbs: a chain's switcher, its node, its
 *  draft state (W10 brief Decided 8). Outside a shell it shows nothing. */
export function HeaderTail({ children }: { children: ReactNode }) {
  const host = useContext(HeaderTailHost);
  return host ? createPortal(children, host) : null;
}
