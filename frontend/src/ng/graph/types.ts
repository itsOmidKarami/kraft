import type { TaskKind } from "../icons";

export type GlyphState = "plain" | "done" | "current" | "todo" | "failed" | "esc" | "ghost" | "amber";
export type GlyphKind = "exec" | "gate" | "slot";

/** What a canvas draws for one node or task. Pages map API data onto this. */
export type GraphItem = {
  id: string;
  label?: string;
  icon?: string;
  taskKind?: TaskKind;
  state?: GlyphState;
  mark?: "add" | "change";
  prob?: boolean;
  capped?: boolean;
  skipped?: boolean;
  attempt?: number;
  attemptStopped?: boolean;
  esc?: boolean;
  running?: boolean;
  paused?: boolean;
  meta?: string;
  metaTone?: "red" | "amber" | "green";
};
