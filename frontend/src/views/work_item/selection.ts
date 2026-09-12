/** The Inspector's five tabs (UI v2 · 05, 11–15). */
export type InspectorTab = "tasks" | "changes" | "documents" | "timeline" | "config";

/** The right pane follows whichever row the Inspector list selected. `null`
 *  (nothing picked yet) renders each pane's own empty state. */
export type Selection =
  | { kind: "session"; id: string | null }
  | { kind: "file"; id: string | null }
  | { kind: "document"; id: string | null }
  | { kind: "timeline-node"; id: string | null };

/** A Changes selection is a file path, but the same path can appear in both
 *  the "This node" and "On this branch" sections. Qualify the landed one so
 *  only the row the user clicked lights up, and so `RightPane/Diff.tsx`
 *  scrolls to that section's occurrence rather than always the in-flight one.
 *  In-flight stays a bare path, which keeps existing `#file=…` links working. */
export type DiffSection = "in-flight" | "landed";

export const fileKey = (section: DiffSection, path: string) =>
  section === "landed" ? `landed:${path}` : path;

export const fileSection = (key: string): DiffSection =>
  key.startsWith("landed:") ? "landed" : "in-flight";

export const filePath = (key: string) =>
  key.startsWith("landed:") ? key.slice("landed:".length) : key;
