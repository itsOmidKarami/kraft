import type { Authored, Change, Problem, Result } from "./types";

/** What the pages read off a resolve result. Pure; nothing here edits a draft (R18). */

export type Step = Authored & { id: string; tasks: Task[] };
export type Task = Authored & { id: string };
export type Container = Authored & { steps?: Step[] };
export type NodeA = Container & { id: string; kind?: "exec" | "gate"; extends?: string };

export const chainFile = (key: string) => `chains/${key}.yaml`;

/** The chain's authored mapping: the last that parsed. */
export const authoredChain = (r: Result, key: string): Authored => r.model[chainFile(key)] ?? {};
export const authoredNodes = (r: Result, key: string): NodeA[] => (authoredChain(r, key).nodes as NodeA[] | undefined) ?? [];

/** `tasks:` is one step `main` (the model's own rule); every container gets `steps`. */
export function normalise<T extends Authored>(c: T | null | undefined): (T & { steps: Step[] }) | null {
  if (!c) return null;
  if (Array.isArray(c.steps)) return c as T & { steps: Step[] };
  if (Array.isArray(c.tasks)) {
    const { tasks, ...rest } = c;
    return { ...rest, steps: [{ id: "main", tasks: tasks as Task[] }] } as unknown as T & { steps: Step[] };
  }
  return { ...c, steps: [] };
}

/** A node as the resolved chain has it (`extends` expanded), steps normalised; null when the draft doesn't resolve. */
export function resolvedNode(r: Result, id: string): NodeA | null {
  const n = (r.resolved?.chain.nodes as NodeA[] | undefined)?.find((x) => x.id === id);
  return n ? (normalise(n) as NodeA) : null;
}

/** A node's kind: written, else what the resolved chain says, else exec. */
export function kindOf(r: Result, n: NodeA): "exec" | "gate" {
  if (n.kind === "exec" || n.kind === "gate") return n.kind;
  return r.resolved?.nodes.find((x) => x.id === n.id)?.kind ?? "exec";
}

const under = (path: string, root: string) => path === root || path.startsWith(`${root}.`);

/** The problems at a path; `deep` adds its children's (a node's ring counts its tasks'). */
export const problemsAt = (r: Result, path: string, deep = false): Problem[] =>
  r.problems.filter((p) => (deep ? under(p.path, path) : p.path === path));

/** The change at a path: its own, else an added or removed ancestor it folds into. */
export function changeAt(r: Result, path: string): Change | undefined {
  const own = r.changes.find((c) => c.path === path);
  if (own) return own;
  return r.changes.find((c) => c.kind !== "change" && path.startsWith(`${c.path}.`));
}

/** Config rows in the server's order (brief Decided 11). `id`, `kind` and `icon` have their own controls. */
const NOT_ROWS = new Set(["id", "kind", "icon"]);
export const sourceRows = (r: Result, path: string) =>
  Object.entries(r.sources[path] ?? {}).filter(([f]) => !NOT_ROWS.has(f)).map(([field, s]) => ({ field, value: s.value, source: s.source }));

/** The header's two numbers. A YAML syntax error counts as a problem. */
export const counts = (r: Result) => ({ changes: r.changes.length, problems: r.problems.length + (r.yaml_error ? 1 : 0) });

/** "library:tasks.implementer" → "library"; the chip's word (Decisions §9 Config tab). */
export const sourceWord = (source: string) => (source === "chain" ? "this chain" : source.startsWith("library:") ? "library" : source);

/** Named containers inside a node (W9's canonical segments). */
const SLOTS = new Set(["on_failure", "fix_loop", "escalation", "on_base_changed", "on_conflict", "auto_review", "judge"]);

/** The component at a canonical path in a node tree (authored or resolved),
 *  walking steps, tasks and the named containers; null when it isn't there. */
export function walk(nodes: NodeA[], path: string): Authored | null {
  const [id, ...rest] = path.split(".");
  let at: Authored | null = nodes.find((n) => n.id === id) ?? null;
  for (const seg of rest) {
    if (!at) return null;
    if (SLOTS.has(seg) && at[seg] !== undefined) {
      at = (at[seg] as Authored | null) ?? null;
      continue;
    }
    // A step's task; ids never collide with `main`, which is reserved.
    const task = Array.isArray(at.tasks) ? (at.tasks as Task[]).find((t) => t.id === seg) : undefined;
    if (task) { at = task; continue; }
    const step = normalise(at)?.steps.find((s) => s.id === seg);
    if (step) { at = step; continue; }
    // The escalation slot holds one task, addressed by its own id.
    if (at.id === seg) continue;
    return null;
  }
  return at;
}

/** The authored component at a path: what this chain itself writes there. */
export const authoredAt = (r: Result, key: string, path: string): Authored | null => (path ? walk(authoredNodes(r, key), path) : authoredChain(r, key));
/** The resolved component at a path (extends expanded), when the draft resolves. */
export const resolvedAt = (r: Result, path: string): Authored | null => (r.resolved ? (path ? walk(r.resolved.chain.nodes as NodeA[], path) : r.resolved.chain) : null);

/** A field's value at a path: the resolved one from `sources`, else what the file writes. */
export function valueAt(r: Result, key: string, path: string, field: string): unknown {
  const s = r.sources[path]?.[field];
  if (s) return s.value;
  const a = resolvedAt(r, path) ?? authoredAt(r, key, path);
  return field.split(".").reduce<unknown>((o, k) => (o && typeof o === "object" ? (o as Authored)[k] : undefined), a);
}
