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
  /** Unchanged while a draft is reviewed (Decisions §9 Publish). */
  faded?: boolean;
};

const STATE_WORD: Record<GlyphState, string> = { plain: "", done: "done", current: "running", todo: "not started", failed: "failed", esc: "escalated", ghost: "removed", amber: "waiting" };

/** A node or task button's accessible name: `<id>, <kind>, <state>[, attempt N]` (spec §5.2).
 *  A current node or task that runs nothing says why in the words drawn for
 *  it ("needs you", "waiting on CI", "paused", "capped"): its glyph is the
 *  running one, but "running" told a screen reader the opposite (R12b-09).
 *  `wait` and `sub` are a chain node's (`layout.ChainNode`). */
export function accessibleName(item: GraphItem & { wait?: string; sub?: string }, kind: string): string {
  const idle = item.state === "current" && !item.running;
  const word = (idle && (item.wait ?? item.sub ?? item.meta)) || STATE_WORD[item.state ?? "plain"];
  return [item.id, kind, word, item.attempt && item.attempt >= 2 ? `attempt ${item.attempt}` : ""].filter(Boolean).join(", ");
}

/** Ids break after underscores, so `approve_spec` wraps in a 138px column. */
export const breakable = (s: string) => s.replace(/_/g, "_​");
