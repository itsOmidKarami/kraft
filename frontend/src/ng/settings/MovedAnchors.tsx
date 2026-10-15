import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";

/** The sections that moved from Appearance to Board & code, so a bookmark to one still lands on it. */
const MOVED = new Set(["#set-code", "#set-diff", "#set-board", "#set-editor"]);

/** Wraps the Appearance route: a hash that now lives on Board & code goes there, anything else stays. */
export function MovedAnchors({ children }: { children: ReactNode }) {
  const { hash } = useLocation();
  return MOVED.has(hash) ? <Navigate to={`/settings/board-and-code${hash}`} replace /> : <>{children}</>;
}
