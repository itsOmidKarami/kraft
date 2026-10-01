import type { ChainNode } from "../../types";
import type { Sel } from "../graph/usePaneSelection";
import { ESCALATION } from "./nodeGraph";
import { stepsOf } from "./paths";

/** Where the item page is: the node view (if any), the selection, its tab and
 *  attempt (spec §6.2), and the document open over it. The URL carries all of them, so Back and shared links land
 *  on them. */
export interface Place {
  node?: string;
  sel: Sel;
  tab?: string;
  attempt?: number;
  /** The document id open over the page (`?doc=`), so ⌘K and a pasted link land on it. */
  doc?: string;
}

/** A selection's canonical path: `node`, `node.step` or `node.step.task`. */
export function selPath(sel: Sel): string | null {
  if (sel.kind === "chain") return null;
  if (sel.kind === "node") return sel.node;
  if (sel.kind === "step") return `${sel.node}.${sel.step}`;
  return `${sel.node}.${sel.step}.${sel.task}`;
}

/** The selection a path names, or null when the chain has no such node, step or task. */
export function pathSel(path: string, nodes: ChainNode[]): Sel | null {
  const [n, step, task, ...rest] = path.split(".");
  const node = nodes.find((x) => x.id === n);
  if (!node || rest.length) return null;
  if (!step) return { kind: "node", node: n };
  // The escalation task is no step of the chain: it hangs off the node (NodeGraph's side branch).
  if (step === ESCALATION && task === ESCALATION) return { kind: "task", node: n, step, task };
  const s = stepsOf(node).steps.find((x) => x.id === step);
  if (!s) return null;
  if (!task) return { kind: "step", node: n, step };
  return s.tasks.some((t) => t.split(".").at(-1) === task) ? { kind: "task", node: n, step, task } : null;
}

/** Read a URL. An unknown node view falls back to the chain; an unknown `sel`
 *  to the floor (the node in a node view, else the chain). */
export function readPlace(nodeParam: string | undefined, search: URLSearchParams, nodes: ChainNode[]): Place {
  const node = nodeParam && nodes.some((n) => n.id === nodeParam) ? nodeParam : undefined;
  const floor: Sel = node ? { kind: "node", node } : { kind: "chain" };
  const raw = search.get("sel");
  const sel = (raw && pathSel(raw, nodes)) || floor;
  const attempt = Number(search.get("attempt"));
  return { node, sel, tab: search.get("tab") ?? undefined, attempt: Number.isInteger(attempt) && attempt > 0 ? attempt : undefined, doc: search.get("doc") || undefined };
}

/** The URL for a place. */
export function placeUrl(id: string, p: Place): string {
  const q = new URLSearchParams();
  const path = selPath(p.sel);
  if (path && path !== p.node) q.set("sel", path);
  if (p.tab) q.set("tab", p.tab);
  if (p.attempt) q.set("attempt", String(p.attempt));
  if (p.doc) q.set("doc", p.doc);
  const qs = q.toString();
  return `/work-items/${encodeURIComponent(id)}${p.node ? `/nodes/${encodeURIComponent(p.node)}` : ""}${qs ? `?${qs}` : ""}`;
}

/** Entering or leaving a node view pushes (Back works); anything else replaces. */
export const pushes = (from: Place, to: Place) => from.node !== to.node;
