import { create } from "zustand";
import type { WorkItem } from "../../types";

export type CrumbItem = Pick<WorkItem, "id" | "repo" | "title" | "bead_id" | "mr_ref" | "display_status">;

/** The item the page in view has loaded, for the header's crumbs: the board
 *  list leaves archived items out, and the item page always has its own. */
export const usePageItem = create<{ item: CrumbItem | null; set: (item: CrumbItem | null) => void }>((set) => ({
  item: null,
  set: (item) => set({ item }),
}));
