import type { NodeStep } from "./nodeLayout";
import type { ChainArc, ChainNode, Seam } from "./layout";
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

/** A 15-node chain mid-run: the fix loop on verification, a rebase arc, open seams after the current node. */
export const CHAIN_15: ChainNode[] = [
  { id: "intake", kind: "exec", icon: "inbox", state: "done", meta: "2m" },
  { id: "spec", kind: "exec", icon: "sparkles", state: "done", meta: "6m" },
  { id: "approve_spec", kind: "gate", state: "done", meta: "you" },
  { id: "plan", kind: "exec", icon: "file-text", state: "done", meta: "4m" },
  { id: "approve_plan", kind: "gate", state: "done", meta: "auto" },
  { id: "implement", kind: "exec", icon: "layers", state: "done", meta: "38m", attempt: 2 },
  { id: "verification", kind: "exec", icon: "shield-check", state: "current", sub: "2/3 · 10:42", attempt: 2, running: true },
  { id: "code_review", kind: "exec", icon: "bot", state: "todo" },
  { id: "review_gate", kind: "gate", state: "todo" },
  { id: "docs", kind: "exec", icon: "scroll-text", state: "todo" },
  { id: "security_scan", kind: "exec", icon: "shield", state: "todo" },
  { id: "merge_request", kind: "exec", icon: "git-pull-request", state: "todo" },
  { id: "mr_checks", kind: "exec", icon: "workflow", state: "todo" },
  { id: "merge_gate", kind: "gate", state: "todo" },
  { id: "close", kind: "exec", icon: "circle-dot", state: "todo" },
];
export const CHAIN_15_ARCS: ChainArc[] = [
  { kind: "loop", node: "verification", tone: "active", label: "fix · round 2" },
  { kind: "rebase", from: "merge_gate", to: "verification" },
  { kind: "reject", from: "review_gate", to: "implement" },
];
export const CHAIN_15_SEAMS: Seam[] = [{ at: 8, open: true }, { at: 10, always: true }];

/** A short chain that stopped: a capped node with an escalation, and a failed one. */
export const CHAIN_STOPPED: ChainNode[] = [
  { id: "intake", kind: "exec", icon: "inbox", state: "done", meta: "1m" },
  { id: "implement", kind: "exec", icon: "layers", state: "current", capped: true, attempt: 3, attemptStopped: true, meta: "capped", metaTone: "red", esc: true },
  { id: "verification", kind: "exec", icon: "shield-check", state: "failed", prob: true, meta: "failed", metaTone: "red" },
  { id: "merge", kind: "gate", state: "todo" },
  { id: "removed_step", kind: "exec", icon: "box", state: "ghost" },
];

/** verification, mid-run: a 3-task parallel step, the fix loop, an escalation branch. */
export const NODE_VERIFY: NodeStep[] = [
  { id: "checks", tasks: [
    { id: "lint", taskKind: "subprocess", state: "done", meta: "11s" },
    { id: "typecheck", taskKind: "subprocess", state: "done", meta: "19s" },
    { id: "unit_tests", taskKind: "builtin", state: "done", meta: "1m 04s", attempt: 2 },
  ] },
  { id: "review", tasks: [{ id: "code_review", taskKind: "agent", icon: "bot", state: "current", meta: "running · 41s", running: true }] },
  { id: "report", tasks: [{ id: "summarise", taskKind: "agent", state: "todo" }] },
];
export const NODE_VERIFY_SIDE = { id: "escalate", icon: "siren", meta: "thread · 2 turns" };

/** A node being edited: a change, an empty step's slot, seams. */
export const NODE_EDIT: NodeStep[] = [
  { id: "scan", mark: "change", seamBefore: true, seamBelow: true, tasks: [
    { id: "semgrep", taskKind: "subprocess", mark: "change" },
    { id: "secrets", taskKind: "subprocess", mark: "add" },
  ] },
  { id: "step_2", mark: "add", tasks: [], slot: {} },
];

/** Placeholder pane bodies per selection kind: the real ones are the pages' (W5 on). */
export const PANE_FACTS: Record<"chain" | "node" | "step" | "task", { sub: string; rows: [string, string][] }> = {
  chain: { sub: "15 nodes · running · verification", rows: [["Status", "running"], ["Progress", "6 of 15 nodes"], ["Current", "verification"], ["Spend", "$3.12 of $25"]] },
  node: { sub: "exec node", rows: [["Status", "see the canvas"], ["Steps", "3"], ["Fix loop", "attempt 2 of 3"], ["Running time", "10:42 of 8h"]] },
  step: { sub: "step · 3 parallel tasks", rows: [["Tasks", "lint, typecheck, unit_tests"], ["Status", "done"]] },
  task: { sub: "task", rows: [["Kind", "agent"], ["Model", "default"], ["Duration", "41s"]] },
};
