import { useEffect } from "react";
import { holdPane } from "./sidebarPref";

/** Marks a page's pane as open: on a narrow screen the sidebar drops to its rail meanwhile. */
export function usePane(open: boolean): void {
  useEffect(() => (open ? holdPane() : undefined), [open]);
}
