import type { GlyphKind, GlyphState, GraphItem } from "./types";
import type { GlyphSize } from "./NodeGlyph";

export const SIZES: GlyphSize[] = ["sm", "md", "lg", "gv"];
export const EXEC_STATES: GlyphState[] = ["plain", "done", "current", "todo", "failed", "esc", "ghost", "amber"];
export const GATE_STATES: GlyphState[] = ["plain", "done", "current", "todo", "ghost"];

/** Marks and badges, at lg: one cell each. */
export const BADGES: { caption: string; kind?: GlyphKind; item: Omit<GraphItem, "id"> & { sel?: boolean } }[] = [
  { caption: "add", item: { icon: "sparkles", mark: "add" } },
  { caption: "add + problem", item: { icon: "sparkles", mark: "add", prob: true } },
  { caption: "change", item: { icon: "layers", mark: "change" } },
  { caption: "problem", item: { icon: "layers", prob: true } },
  { caption: "selected", item: { icon: "layers", state: "current", sel: true } },
  { caption: "capped", item: { icon: "layers", state: "current", capped: true, attempt: 3, attemptStopped: true } },
  { caption: "skipped", item: { icon: "shield", skipped: true } },
  { caption: "attempt 1", item: { icon: "layers", state: "done", attempt: 1 } },
  { caption: "attempt 2", item: { icon: "layers", state: "current", attempt: 2, running: true } },
  { caption: "escalated", item: { icon: "layers", state: "current", esc: true } },
  { caption: "paused", item: { icon: "layers", state: "current", paused: true, running: true } },
  { caption: "unknown icon", item: { icon: "no-such-icon", taskKind: "subprocess" } },
  { caption: "slot", kind: "slot", item: {} },
  { caption: "gate + attempt", kind: "gate", item: { state: "current", attempt: 2 } },
];
