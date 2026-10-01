import type { PaneKind } from "../templates/panes/describe";
import type { Authored, Result } from "../templates/draft/types";
import { authoredAt } from "../templates/draft/view";
import { parseRef, type Section } from "./types";

export type LibKind = PaneKind | "steering";

export interface LibDescription {
  kind: LibKind;
  /** The component this path is in: `nodes.verification`. */
  component: string;
  section: Section;
  /** The last segment: what the pane is titled. */
  id: string;
  /** The path inside a node's own definition (`review.code_review`), or "" at a component's root. */
  inside: string;
}

const CONTAINERS = new Set(["on_failure", "fix_loop", "escalation", "on_base_changed", "on_conflict"]);

/** What a library path names (`tasks.implementer`, `nodes.verification.review.code_review`, `steering.x`), for its pane. */
export function libDescribe(r: Result, path: string): LibDescription | null {
  const segs = path.split(".");
  const ref = parseRef(segs.slice(0, 2).join("."));
  if (!ref) return null;
  const component = `${ref.section}.${ref.name}`;
  const rest = segs.slice(2);
  const last = segs[segs.length - 1];
  const base = { component, section: ref.section, id: last, inside: rest.join(".") };
  if (!rest.length) {
    const own = authoredAt(r, { area: "library", key: "library" }, path) as Authored | null;
    const kind: LibKind = ref.section === "nodes" ? (own?.kind === "gate" ? "gate" : "node") : ref.section === "steps" ? "step" : ref.section === "tasks" ? "task" : "steering";
    return { ...base, kind };
  }
  if (last === "fix_loop") return { ...base, kind: "fixloop", id: "fix loop" };
  if (last === "judge" && segs[segs.length - 2] === "fix_loop") return { ...base, kind: "judge", id: "judge" };
  if (last === "auto_review") return { ...base, kind: "review", id: "auto_review" };
  if (segs[segs.length - 2] === "escalation") return { ...base, kind: "esc" };
  // `steps.checks.lint` is a task; in a node, `review` is a step and `review.code_review` its task.
  if (ref.section === "steps") return { ...base, kind: "task" };
  const below = CONTAINERS.has(rest[0]) ? rest.slice(1) : rest;
  return { ...base, kind: below.length >= 2 ? "task" : "step" };
}

/** The pane's crumbs after "Library": the component and each container above the selection, each a path to select. */
export function libCrumbs(path: string): { label: string; path: string }[] {
  const segs = path.split(".");
  if (segs.length <= 2) return [{ label: segs[0], path: "" }];
  return [{ label: segs[1], path: segs.slice(0, 2).join(".") }, ...segs.slice(2, -1).map((s, i) => ({ label: s, path: segs.slice(0, i + 3).join(".") }))];
}
