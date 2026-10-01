import type { Authored, Change, Problem, Result, Scope } from "./types";

/** What the pages read off a resolve result. Pure; nothing here edits a draft (R18). */

export type Step = Authored & { id: string; tasks: Task[] };
export type Task = Authored & { id: string };
export type Container = Authored & { steps?: Step[] };
export type NodeA = Container & { id: string; kind?: "exec" | "gate"; extends?: string };

export const chainFile = (key: string) => `chains/${key}.yaml`;
export const LIBRARY_FILE = "library.yaml";

/** The chain file a draft edits: its key's, unless a chain `rename` moved the
 *  text to `chains/<new id>.yaml` (the draft keeps its old key, W9). */
export function liveChainFile(files: Record<string, unknown>, key: string): string {
  const own = chainFile(key);
  if (files[own] !== null) return own;
  return Object.keys(files).find((f) => f !== own && f.startsWith("chains/") && files[f] !== null) ?? own;
}

/** The one file a draft's text edits: the library's, or the chain's (moved by a rename). */
export const scopeFile = (files: Record<string, unknown>, scope: Scope): string => (scope.area === "library" ? LIBRARY_FILE : liveChainFile(files, scope.key));

/** The draft's authored mapping: a chain's file, or `library.yaml`; the last that parsed. */
export const authoredChain = (r: Result, scope: Scope): Authored =>
  scope.area === "library" ? r.model[LIBRARY_FILE] ?? {} : r.model[liveChainFile(r.model, scope.key)] ?? r.model[chainFile(scope.key)] ?? {};
/** A chain's node list; the library has none (its nodes are a mapping by name). */
export const authoredNodes = (r: Result, scope: Scope): NodeA[] => (scope.area === "library" ? [] : (authoredChain(r, scope).nodes as NodeA[] | undefined) ?? []);

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

/** A library component's path `<section>.<name>[.<step>[.<task>]]`, walked as a node tree under `name`. */
function libraryAt(root: Authored, path: string): Authored | null {
  const [section, name, ...rest] = path.split(".");
  const entry = (root[section] as Record<string, Authored> | undefined)?.[name];
  if (!entry) return null;
  return rest.length ? walk([{ ...entry, id: name }], [name, ...rest].join(".")) : entry;
}

/** The authored component at a path: what this draft's file itself writes there. */
export const authoredAt = (r: Result, scope: Scope, path: string): Authored | null =>
  !path ? authoredChain(r, scope) : scope.area === "library" ? libraryAt(authoredChain(r, scope), path) : walk(authoredNodes(r, scope), path);
/** The resolved component at a path (extends expanded), when the draft resolves. */
export const resolvedAt = (r: Result, path: string): Authored | null => (r.resolved ? (path ? walk(r.resolved.chain.nodes as NodeA[], path) : r.resolved.chain) : null);

const dig = (a: Authored | null | undefined, field: string) => field.split(".").reduce<unknown>((o, k) => (o && typeof o === "object" ? (o as Authored)[k] : undefined), a);

/** A library component's value: what it writes, else what the component it `extends` writes, up its bases.
 *  The library has no `sources` (a bead asks for them); a task extends a task, a node a node. */
function libraryValue(r: Result, scope: Scope, path: string, field: string): unknown {
  const root = authoredChain(r, scope) as Record<string, Record<string, Authored> | undefined>;
  let at = authoredAt(r, scope, path);
  const section = path.startsWith("nodes.") && path.split(".").length === 2 ? "nodes" : "tasks";
  for (let i = 0; i < 8 && at; i++) {
    const v = dig(at, field);
    if (v !== undefined) return v;
    at = typeof at.extends === "string" ? root[section]?.[at.extends] ?? null : null;
  }
  return undefined;
}

/** A field's value at a path: the resolved one from `sources`, else what the file writes. */
export function valueAt(r: Result, scope: Scope, path: string, field: string): unknown {
  const s = r.sources[path]?.[field];
  if (s) return s.value;
  if (scope.area === "library") return libraryValue(r, scope, path, field);
  return dig(resolvedAt(r, path) ?? authoredAt(r, scope, path), field);
}
