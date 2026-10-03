import type { Result, Scope } from "../draft/types";
import { authoredNodes, kindOf, liveChainId } from "../draft/view";
import { isTaskPath } from "../sel";

export type PaneKind = "chain" | "node" | "gate" | "step" | "task" | "fixloop" | "judge" | "esc" | "review";

/** What a canonical path names, for the pane: its kind, its node, its own id. */
export function describe(r: Result, scope: Scope, path: string): { kind: PaneKind; node: string; id: string } {
  // A chain the draft renamed is titled by its new id, as it will publish (R10b-02).
  if (!path) return { kind: "chain", node: "", id: scope.area === "chains" ? liveChainId(r.model, scope.key) : scope.key };
  const segs = path.split(".");
  const [node] = segs;
  const last = segs[segs.length - 1];
  if (segs.length === 1) {
    const n = authoredNodes(r, scope).find((x) => x.id === node);
    return { kind: n && kindOf(r, n) === "gate" ? "gate" : "node", node, id: node };
  }
  if (last === "fix_loop") return { kind: "fixloop", node, id: "fix loop" };
  if (last === "judge" && segs[segs.length - 2] === "fix_loop") return { kind: "judge", node, id: "judge" };
  if (last === "auto_review") return { kind: "review", node, id: "auto_review" };
  if (segs[segs.length - 2] === "escalation") return { kind: "esc", node, id: last };
  if (isTaskPath(path, r.resolved?.task_paths)) return { kind: "task", node, id: last };
  return { kind: "step", node, id: last };
}

const WORD: Record<string, string> = { on_failure: "on failure", fix_loop: "fix loop", escalation: "escalation", on_conflict: "on conflict", auto_review: "auto review" };

/** The pane's crumb (Decisions §9 Pane crumb): the parent path, each segment a
 *  link to its level. `on_base_changed` is not a level; a lone `main` step is
 *  not shown. A word segment ("on failure") selects its owner. */
export function crumbPath(path: string, loneMain: (prefix: string) => boolean): { label: string; path: string }[] {
  const segs = path.split(".");
  const out: { label: string; path: string }[] = [];
  for (let i = 0; i < segs.length - 1; i++) {
    const seg = segs[i];
    const prefix = segs.slice(0, i + 1).join(".");
    if (seg === "on_base_changed") continue;
    if (seg === "main" && loneMain(segs.slice(0, i).join("."))) continue;
    const owner = WORD[seg] ? segs.slice(0, seg === "on_conflict" ? i - 1 : i).join(".") : prefix;
    out.push({ label: WORD[seg] ?? seg, path: seg === "fix_loop" ? prefix : owner });
  }
  return out;
}
