import { useStore } from "../../../store";
import { groupOf } from "../../board/model";

/** The Needs you count: the board's chip and the tab badge both read it, so the two cannot disagree. */
export function useNeedsCount(): number {
  return useStore((s) => Object.values(s.workItems).filter((i) => groupOf(i) === "needs").length);
}
