import { create } from "zustand";

/** The nodes the one-off review is about, drawn on the item's graph while its dialog is open. */
export const useOneOffMarks = create<{ marks: Record<string, "add" | "change">; set: (m: Record<string, "add" | "change">) => void }>((set) => ({
  marks: {},
  set: (marks) => set({ marks }),
}));
