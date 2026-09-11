/** The Inspector's five tabs (UI v2 · 05, 11–15). */
export type InspectorTab = "tasks" | "changes" | "documents" | "timeline" | "config";

/** The right pane follows whichever row the Inspector list selected. `null`
 *  (nothing picked yet) renders each pane's own empty state. */
export type Selection =
  | { kind: "session"; id: string | null }
  | { kind: "file"; id: string | null }
  | { kind: "document"; id: string | null }
  | { kind: "timeline-node"; id: string | null };

export const EMPTY_SELECTION: Record<InspectorTab, Selection> = {
  tasks: { kind: "session", id: null },
  changes: { kind: "file", id: null },
  documents: { kind: "document", id: null },
  timeline: { kind: "timeline-node", id: null },
  config: { kind: "session", id: null }, // unused — Config has no right pane
};
