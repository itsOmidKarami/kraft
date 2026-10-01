import type { Authored } from "../templates/draft/types";
import { idError } from "../templates/ids";

/** The handler and slot segments a canonical path passes through: what lives in one is not moved (R47). */
const HANDLER = new Set(["on_failure", "fix_loop", "escalation", "judge", "auto_review", "on_base_changed", "on_conflict"]);

export type Movable = { section: "nodes" | "tasks"; word: "exec node" | "task" };

/** What Move to library offers for a chain component: an exec node or a task that extends nothing, outside any
 *  handler or slot. A gate, a step, a judge, an escalation, a reviewer and a component that already extends a
 *  library one are not offered. */
export function movable(path: string, own: Authored | null, kind: "node" | "gate" | "step" | "task" | "fixloop" | "judge" | "esc" | "review" | "chain"): Movable | null {
  if (!own || typeof own.extends === "string" || path.split(".").some((s) => HANDLER.has(s))) return null;
  if (kind === "node") return { section: "nodes", word: "exec node" };
  if (kind === "task") return { section: "tasks", word: "task" };
  return null;
}

/** What the card says will happen, from the component as the chain writes it. */
export function moveLines(chain: string, section: "nodes" | "tasks", name: string, id: string, own: Authored): { gains: string; keeps: string } {
  const keys = Object.keys(own).filter((k) => k !== "id");
  const what = section === "nodes" ? "exec node" : `${typeof own.kind === "string" ? own.kind : "task"} task`;
  return {
    gains: `The library gains ${section}.${name} (${what}, ${keys.length} ${keys.length === 1 ? "key" : "keys"}).`,
    keeps: `${chain} keeps { id: ${id}, extends: ${name} }.`,
  };
}

/** The card's id check: the id rule, then the section's names (the published library's and the draft's own). */
export const nameError = (name: string, taken: string[]) => idError(name, taken);
