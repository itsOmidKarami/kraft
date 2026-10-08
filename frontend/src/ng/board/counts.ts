import { useStore } from "../../store";
import type { WorkItem } from "../../types";
import { ENDED_STATUSES } from "../../types/vocab.generated";
import { groupOf, type GroupKey } from "./model";

/** The board's counts, defined once. "N need you" and "N running" are the
 *  size of the board's Needs you and Running groups (`groupOf`): the board's
 *  header and groups, the sidebar, the item page's "others need you", and the
 *  phone's tab and chips all count with this, so no two of them disagree. */
export function countIn(items: Iterable<Pick<WorkItem, "display_status" | "current_node_id">>, g: GroupKey): number {
  let n = 0;
  for (const i of items) if (groupOf(i) === g) n++;
  return n;
}

/** `countIn` over every item the board knows; `except` leaves one out (the item page's own). */
export function useGroupCount(g: GroupKey, except?: string): number {
  return useStore((s) => countIn(Object.values(s.workItems).filter((i) => i.id !== except), g));
}

/** Stored statuses that end an item (generated from the server's `ENDED`). */
const ENDED = new Set<string>(ENDED_STATUSES);

/** Not ended: every Needs you, Running and Not started row. What a repo's
 *  disconnect waits on and what keeps its version through a publish; the
 *  server counts the same set (`open_counts_by_repo`), and the screens call
 *  it "open", never "running", which is the board's Running group alone. */
export const isOpen = (i: Pick<WorkItem, "status">) => !ENDED.has(i.status);
