import { stepsGlyph } from "../../item/chainValues";
import type { Authored, Change, NodeInheritance, Origin, Problem, Result, Scope } from "./types";

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
/** The chain's id as the draft holds it: its key, or the id a `rename` moved it to. */
export const liveChainId = (files: Record<string, unknown>, key: string): string => liveChainFile(files, key).replace(/^chains\//, "").replace(/\.yaml$/, "");
export const scopeFile = (files: Record<string, unknown>, scope: Scope): string => (scope.area === "library" ? LIBRARY_FILE : liveChainFile(files, scope.key));

/** The draft's authored mapping: a chain's file, or `library.yaml`; the last that parsed. */
export const authoredChain = (r: Result, scope: Scope): Authored =>
  scope.area === "library" ? r.model[LIBRARY_FILE] ?? {} : r.model[liveChainFile(r.model, scope.key)] ?? r.model[chainFile(scope.key)] ?? {};
/** The chain a chain draft extends, as written; undefined for one with its own `nodes`. */
export const extendsOf = (r: Result, scope: Scope): string | undefined => {
  if (scope.area === "library") return undefined;
  const parent = authoredChain(r, scope).extends;
  return typeof parent === "string" ? parent : undefined;
};

/** A chain's node list; the library has none (its nodes are a mapping by name). An
 *  extending chain writes no `nodes`, so the canvas draws them with its edits applied:
 *  the draft's expansion, there while it does not validate, else the resolved chain. */
export const authoredNodes = (r: Result, scope: Scope): NodeA[] => {
  if (scope.area === "library") return [];
  if (extendsOf(r, scope)) return ((r.extending?.nodes ?? r.resolved?.chain?.nodes ?? []) as NodeA[]).map((n) => normalise(n) as NodeA);
  return (authoredChain(r, scope).nodes as NodeA[] | undefined) ?? [];
};

/** Where a node of an extending chain comes from: inherited, added or overridden here. */
export const originOf = (r: Result, id: string): Origin | undefined => r.extending?.origins[id];

/** What a child add-on's chain overrides or adds at node `id`, as the server marked it. */
export const inheritedAt = (r: Result, id: string): NodeInheritance | undefined => r.resolved?.inherited_nodes?.[id];

/** The bare name of an `inherited_nodes` entry: the server files it as `<section>.<qualified name>`
 *  (`tasks.platform-dept:security_scan`), and the mark shows what follows the last colon, or
 *  what follows the section dot when the name is unqualified (`tasks.security_scan`). */
export const inheritedName = (entry: string): string => {
  const rest = entry.slice(entry.indexOf(".") + 1);
  return rest.slice(rest.lastIndexOf(":") + 1);
};

/** Whether node `id` is one an extending chain adds: read off its file's `add` entries, which
 *  hold while the edits don't apply and `origins` is empty. */
export const isAdded = (r: Result, scope: Scope, id: string): boolean => addedNodes(r, scope).some((n) => n.id === id);

/** An extending chain's own nodes: each `add` entry's `node`, as its file writes it. */
export const addedNodes = (r: Result, scope: Scope): NodeA[] =>
  ((authoredChain(r, scope).add as { node?: NodeA }[] | undefined) ?? []).map((e) => e?.node).filter((n): n is NodeA => !!n && typeof n === "object");

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
  const n = (r.resolved?.chain?.nodes as NodeA[] | undefined)?.find((x) => x.id === id);
  return n ? (normalise(n) as NodeA) : null;
}

/** The glyph an exec node draws from its resolved steps (CG-5): the one the item page draws for it. */
export const nodeGlyph = (r: Result, id: string) => stepsGlyph(resolvedNode(r, id)?.steps ?? []);

/** A node's kind: written, else what the resolved chain says, else exec. */
export function kindOf(r: Result, n: NodeA): "exec" | "gate" {
  if (n.kind === "exec" || n.kind === "gate") return n.kind;
  return r.resolved?.nodes.find((x) => x.id === n.id)?.kind ?? "exec";
}

const under = (path: string, root: string) => path === root || path.startsWith(`${root}.`);

/** What a problem names as its place: its path, else the chain itself (the server sends `path: null` for a chain-level problem). */
export const problemPath = (p: Problem): string => p.path ?? "chain";

/** The problems at a path; `deep` adds its children's (a node's ring counts its tasks'). */
export const problemsAt = (r: Result, path: string, deep = false): Problem[] =>
  r.problems.filter((p) => (deep ? under(p.path ?? "", path) : p.path === path));

/** The change at a path: its own, else an added or removed ancestor it folds into. */
export function changeAt(r: Result, path: string): Change | undefined {
  const own = r.changes.find((c) => c.path === path);
  if (own) return own;
  return r.changes.find((c) => c.kind !== "change" && path.startsWith(`${c.path}.`));
}

/** Config rows in the server's order (brief Decided 11). `id`, `kind` and `icon` have their own controls. */
const NOT_ROWS = new Set(["id", "kind", "icon"]);
export const sourceRows = (r: Result, path: string) =>
  Object.entries(r.sources[path] ?? {}).filter(([f]) => !NOT_ROWS.has(f)).map(([field, s]) => ({ field, value: s.value, source: s.source, recovery: s.recovery }));

/** A node's Config rows, a read-only one carrying why it is locked: a node with a fix loop cannot be read only (the prototype's lock). */
export function settingRows(r: Result, path: string) {
  const rows: (ReturnType<typeof sourceRows>[number] & { locked?: string })[] = sourceRows(r, path);
  if (resolvedAt(r, path)?.fix_loop) rows.forEach((x) => { if (x.field === "read_only") x.locked = "A node with a fix loop cannot be read only"; });
  return rows;
}

/** The Config rows of an extending chain's component while the draft does not resolve (so it has no
 *  `sources`): each value with the edits applied, `chain` where this chain's file sets it, else
 *  `inherited`, since where an inherited one comes from shows once the draft resolves. */
export function expandedRows(r: Result, scope: Scope, path: string): { field: string; value: unknown; source: string }[] {
  if (!extendsOf(r, scope) || !path) return [];
  const at = walk(authoredNodes(r, scope), path);
  if (!at) return [];
  const own = authoredAt(r, scope, path) ?? {};
  const added = isAdded(r, scope, path.split(".")[0]);
  const rows: { field: string; value: unknown; source: string }[] = [];
  for (const [k, v] of Object.entries(at)) {
    if (NOT_ROWS.has(k) || NESTED.has(k)) continue;
    const pairs = k === "policy" && v && typeof v === "object" ? Object.entries(v).map(([c, x]) => [`policy.${c}`, x] as const) : [[k, v] as const];
    for (const [field, value] of pairs) rows.push({ field, value, source: added || dig(own, field) !== undefined ? "chain" : "inherited" });
  }
  return rows;
}
/** Keys holding nested components, which have panes of their own. */
const NESTED = new Set(["tasks", "steps", "judge", "on_failure", "fix_loop", "escalation", "auto_review", "extends"]);

/** The chain's own caps, in two groups: `sources` has no row for the chain, so these read the file and say "policy" when unset. */
export function chainCapGroups(r: Result, scope: Scope): { head: string; rows: { field: string; value: unknown; source: string }[] }[] {
  const policy = (authoredAt(r, scope, "")?.policy ?? {}) as Record<string, unknown>;
  const row = (f: string) => ({ field: `policy.${f}`, value: policy[f] ?? null, source: policy[f] === undefined ? "policy" : "chain" });
  return [
    { head: "Chain-wide time caps", rows: ["time_cap_minutes", "total_time_cap_minutes"].map(row) },
    { head: "Fix loops in this chain", rows: ["max_attempts", "timeout_minutes"].map(row) },
  ];
}

/** Whether the draft changed this Config row: a change at its path names the field, or names `policy` (a policy key is reported by the group) and the row is a `policy.*` key the file sets. */
export const rowChanged = (r: Result, path: string, field: string, source?: string) =>
  r.changes.some((c) => c.path === path && ((c.fields ?? []).includes(field) || (field.startsWith("policy.") && source === "chain" && (c.fields ?? []).includes("policy"))));

/** The header's two numbers. A YAML syntax error counts as a problem. */
export const counts = (r: Result) => ({ changes: r.changes.length, problems: r.problems.length + (r.yaml_error ? 1 : 0) });

/** "library:tasks.implementer" → "library"; the chip's word (Decisions §9 Config tab). */
export const sourceWord = (source: string) =>
  source === "chain" ? "this chain" : source.startsWith("chain:") ? source.slice("chain:".length) : source.startsWith("library:") ? "library" : source;

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

/** The authored component at a path: what this draft's file itself writes there. On an
 *  extending chain, an added node's component, or an inherited one's override. */
export function authoredAt(r: Result, scope: Scope, path: string): Authored | null {
  if (!path) return authoredChain(r, scope);
  if (scope.area === "library") return libraryAt(authoredChain(r, scope), path);
  if (!extendsOf(r, scope)) return walk(authoredNodes(r, scope), path);
  const added = addedNodes(r, scope).map((n) => normalise(n) as NodeA);
  if (added.some((n) => n.id === path.split(".")[0])) return walk(added, path);
  return (authoredChain(r, scope).overrides as Record<string, Authored> | undefined)?.[path] ?? null;
}
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
  // An extending chain's file holds only its overrides: its nodes with the edits applied hold the rest.
  const expanded = extendsOf(r, scope) ? walk(authoredNodes(r, scope), path) : null;
  return dig(resolvedAt(r, path) ?? expanded ?? authoredAt(r, scope, path), field);
}
