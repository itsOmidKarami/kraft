import { useState, type ReactNode } from "react";
import { HeaderActionsHost } from "../shell/HeaderActions";

/** Stands in for the shell's header, so a page's header buttons render. */
export function WithHeader({ children }: { children: ReactNode }) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  return (
    <>
      <div ref={setHost} />
      <HeaderActionsHost.Provider value={host}>{children}</HeaderActionsHost.Provider>
    </>
  );
}
