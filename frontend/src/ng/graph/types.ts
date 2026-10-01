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
  /** An edit to it is on its way to the server (W10 brief Decided 3). */
  pending?: boolean;
};

const STATE_WORD: Record<GlyphState, string> = { plain: "", done: "done", current: "running", todo: "not started", failed: "failed", esc: "escalated", ghost: "removed", amber: "waiting" };

/** A node or task button's accessible name: `<id>, <kind>, <state>[, attempt N]` (spec §5.2). */
export function accessibleName(item: GraphItem, kind: string): string {
  const word = STATE_WORD[item.state ?? "plain"];
  return [item.id, kind, word, item.attempt && item.attempt >= 2 ? `attempt ${item.attempt}` : ""].filter(Boolean).join(", ");
}

/** Ids break after underscores, so `approve_spec` wraps in a 138px column. */
export const breakable = (s: string) => s.replace(/_/g, "_​");
