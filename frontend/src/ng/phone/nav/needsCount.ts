import { useGroupCount } from "../../board/counts";

/** The Needs you count: the board's chip and the tab badge both read it, so the two cannot disagree. */
export const useNeedsCount = (): number => useGroupCount("needs");
