import type { Harnesses, Repo } from "../../types";
import { KIND_ICON, type TaskKind } from "../icons";
import type { ItemDetail } from "./useItem";

/** The item's own chain as intake froze it (`materialized_chain`): what a
 *  not-yet-started item's Config rows read the chain's values from. Only the
 *  parts those rows show are typed. */
type Caps = Record<string, number | null | undefined>;
type Pol = Record<string, unknown> | null | undefined;
export type MTask = { id: string; kind: string; ref?: string | null; execution?: string | null; skill?: string | null; produces?: string | null; harness?: string | null; model?: string | null; effort?: string | null; profile?: string | null; prompt?: string | null; command?: unknown; policy?: Pol };
type MStep = { id: string; tasks: MTask[]; policy?: Pol };
type MLoop = { max_attempts?: number | null; tasks?: MTask[] | null; steps?: MStep[] | null; judge?: MTask | null };
export type MNode = { id: string; kind: string; tasks?: MTask[] | null; steps?: MStep[] | null; policy?: Pol; fix_loop?: MLoop | null; auto_review?: MTask | null; message?: string | null; artifact?: string | null };
/** What the item runs against, frozen at intake: a workspace's root and its members, in the order a fanned-out task visits them. */
export type MTarget = { kind: string; root?: string | null; mounts?: Record<string, { repository: string; path: string }> };
export type Materialized = {
  chain: { nodes: MNode[]; policy?: Pol };
  target?: MTarget | null;
  policy?: Record<string, unknown> & { cap_defaults?: Record<string, Caps>; maxima?: Record<string, unknown> };
};
/** The item's own policy override (`policy_override`): item-wide fields, and `paths` by canonical path. */
export type ItemPolicy = (Record<string, unknown> & { paths?: Record<string, Record<string, unknown>> }) | null | undefined;

/** A value as the chain (or what stands in for it) gives it, and where it comes from: the chip a row shows. */
export type Given = { value: string; source: string };

export const ENDED = new Set(["done", "cancelled", "archived"]);

/** Filed and never started: no node has run, and it has not ended. */
export const notStarted = (item: Pick<ItemDetail, "current_node_id" | "display_status">) => !item.current_node_id && !ENDED.has(item.display_status ?? "");

/** The frozen chain, or null when the item carries none (or one this reader cannot parse). */
export function materialized(item: { materialized_chain?: string | null }): Materialized | null {
  if (!item.materialized_chain) return null;
  try {
    const m = JSON.parse(item.materialized_chain) as Materialized;
    return Array.isArray(m?.chain?.nodes) ? m : null;
  } catch {
    return null;
  }
}

/** A node's steps: a `tasks:` node is one step called `main`. */
export const stepsOfNode = (n: MNode): MStep[] => n.steps ?? (n.tasks ? [{ id: "main", tasks: n.tasks }] : []);

/** A fix loop's own task by what follows `<node>.fix_loop.`: `judge`, or `<step>.<task>`, where a loop written as a
 *  bare `tasks:` list is one step called `main` (`task-group-shorthand-resolves-to-one-step`). */
function loopTaskAt(node: MNode | undefined, rest: string[]): MTask | undefined {
  const loop = node?.fix_loop;
  if (!loop || !rest.length) return undefined;
  if (rest.length === 1 && rest[0] === "judge") return loop.judge ?? undefined;
  if (rest.length !== 2) return undefined;
  const steps = loop.steps ?? (loop.tasks ? [{ id: "main", tasks: loop.tasks }] : []);
  return steps.find((x) => x.id === rest[0])?.tasks.find((x) => x.id === rest[1]);
}

/** The node, step and task a path names, broadest first. */
function scopes(m: Materialized, path: string) {
  const [n, s, t] = path.split(".");
  const node = m.chain.nodes.find((x) => x.id === n);
  const step = node && s ? stepsOfNode(node).find((x) => x.id === s) : undefined;
  // A gate's reviewer is `<gate>.auto_review`: no step of the chain, but a task of its own.
  const task = step && t ? step.tasks.find((x) => x.id === t) : node?.kind === "gate" && s === "auto_review" && !t ? node.auto_review ?? undefined : s === "fix_loop" ? loopTaskAt(node, path.split(".").slice(2)) : undefined;
  return { node, step, task };
}

export const taskAt = (m: Materialized, path: string) => scopes(m, path).task;

/** The plan task's path: the one agent task with no `skill` in the first node whose own steps hold
 *  one (`progress.implementing_nodes`; a fix loop, `on_failure` or escalation task does not count).
 *  None when that node holds more than one, or the chain has none. */
export function planTaskPath(m: Materialized | null): string | null {
  for (const n of m?.chain.nodes ?? []) {
    const found = stepsOfNode(n).flatMap((st) => st.tasks.filter((t) => t.kind === "agent" && t.skill == null).map((t) => `${n.id}.${st.id}.${t.id}`));
    if (found.length) return found.length === 1 ? found[0] : null;
  }
  return null;
}

const glyphKind = (k: string | undefined): TaskKind | undefined => (k && k in KIND_ICON ? (k as TaskKind) : undefined);

/** What a node's glyph draws (WI-3): its tasks' kind when they share one (agent ✦, builtin ⚙,
 *  subprocess >_, forge git-pull), else none. The API's chain lists bare task ids; the frozen chain has the kinds. */
export function nodeTaskKind(m: Materialized | null, id: string): TaskKind | undefined {
  const node = m && nodeAt(m, id);
  return stepsGlyph(node ? stepsOfNode(node) : []).taskKind;
}

/** A node's glyph from its steps, the item page's rule (WI-3) and the Chains editor's (CG-5): several steps draw
 *  layers, else the kind its tasks share. Neither: the caller's own icon, or the cube. */
export function stepsGlyph(steps: { tasks: Record<string, unknown>[] }[]): { icon?: string; taskKind?: TaskKind } {
  const kinds = new Set(steps.flatMap((s) => s.tasks.map((t) => t.kind)));
  return { icon: steps.length > 1 ? "layers" : undefined, taskKind: kinds.size === 1 ? glyphKind(String([...kinds][0])) : undefined };
}

/** The task that writes the document a gate decides on (its `artifact`), by path: the review brief's "written by". */
export function producerOf(m: Materialized | null, gateId: string): string | null {
  const kind = m && nodeAt(m, gateId)?.artifact;
  if (!kind) return null;
  for (const n of m.chain.nodes)
    for (const st of stepsOfNode(n)) for (const t of st.tasks) if (t.produces === kind) return `${n.id}.${st.id}.${t.id}`;
  return null;
}

/** What a node's fix loop launches, by path from the frozen chain: its repair tasks in order, and its judge when it has one. */
export function loopPaths(m: Materialized | null, node: string): { repair: string[]; judge: string | null } {
  const loop = m && nodeAt(m, node)?.fix_loop;
  const at = `${node}.fix_loop`;
  return {
    repair: (loop ? loop.steps ?? (loop.tasks ? [{ id: "main", tasks: loop.tasks }] : []) : []).flatMap((st) => st.tasks.map((t) => `${at}.${st.id}.${t.id}`)),
    judge: loop?.judge ? `${at}.judge` : null,
  };
}

/** A task's kind by its path, from the frozen chain. */
export const taskKindAt = (m: Materialized | null, path: string) => glyphKind(m ? taskAt(m, path)?.kind : undefined);
export const nodeAt = (m: Materialized, id: string) => m.chain.nodes.find((n) => n.id === id);

/** What a gate says when it waits (its `message:`), from the frozen chain; the API's chain listing carries none. */
export const gateMessage = (m: Materialized | null, id: string): string | undefined => (m && nodeAt(m, id)?.message) || undefined;

/** Every task a node launches: its steps', and its fix loop's repair and judge. */
const nodeTasks = (n: MNode): MTask[] => [
  ...stepsOfNode(n).flatMap((s) => s.tasks),
  ...(n.fix_loop?.tasks ?? []),
  ...(n.fix_loop?.steps ?? []).flatMap((s) => s.tasks),
  ...(n.fix_loop?.judge ? [n.fix_loop.judge] : []),
];
/** The agent tasks under a node, or under the whole chain. */
export const agentTasks = (m: Materialized, node?: string): MTask[] =>
  m.chain.nodes.filter((n) => node === undefined || n.id === node).flatMap(nodeTasks).filter((t) => t.kind === "agent");

const LEVELS = ["work_item", "nodes", "steps", "tasks"];
const num = (v: unknown) => (typeof v === "number" ? v : v === "none" ? Infinity : null);
const lower = (a: number | null, b: number) => (a == null ? b : Math.min(a, b));

/** A cap (`time_cap_minutes`, `total_time_cap_minutes`, `budget_usd`,
 *  `token_budget`) as the scope at `path` runs under it, the way the server's
 *  `policy_for` resolves it: the item's base policy, then each layer the chain
 *  authored over the path, each only lowering it; the item's own policy
 *  item-wide (which replaces the base where the chain sets none) and then on
 *  each enclosing path; else the level's default; all held to the nearest
 *  maximum. Null: no cap. The source names the layer the value came from. */
export function capAt(m: Materialized, path: string, key: string, item?: ItemPolicy): { value: number | null; source: string } {
  const { node, step, task } = scopes(m, path);
  const level = task ? "tasks" : step ? "steps" : "nodes";
  let value = num(m.policy?.[key]);
  let source = "policy";
  let own = false;
  for (const l of [m.chain.policy, node?.policy, step?.policy, task?.policy]) {
    const v = num(l?.[key]);
    if (v == null) continue;
    own = true;
    if (value == null || v <= value) [value, source] = [v, "chain"];
  }
  const segments = path.split(".");
  const layers = [item, ...segments.map((_, i) => item?.paths?.[segments.slice(0, i + 1).join(".")])];
  layers.forEach((l, i) => {
    const v = num(l?.[key]);
    if (v == null) return;
    // Item-wide, a cap replaces the base wherever the chain set none; on a path it only lowers.
    if (i === 0 && !own) [value, source] = [v, "item policy"];
    else if (value == null || v <= value) [value, source] = [lower(value, v), "item policy"];
  });
  if (value == null) [value, source] = [num(m.policy?.cap_defaults?.[level]?.[key]), "policy"];
  for (const at of LEVELS.slice(0, LEVELS.indexOf(level) + 1).reverse()) {
    const bound = num((m.policy?.maxima?.[at] as Caps | undefined)?.[key]);
    if (bound == null) continue;
    if (value == null || bound < value) [value, source] = [bound, "policy maximum"];
    break;
  }
  return { value: value === Infinity ? null : value, source };
}

/** A node's fix-loop attempts: the item's own policy for the node (`walk._item_cap`), else the loop's. */
export function attemptsAt(m: Materialized, node: string, item?: ItemPolicy): Given | null {
  const own = num(item?.paths?.[node]?.max_attempts) ?? num(item?.max_attempts);
  if (own != null) return { value: String(own), source: "item policy" };
  const loop = nodeAt(m, node)?.fix_loop?.max_attempts;
  return loop != null ? { value: String(loop), source: "chain" } : null;
}

const providerOf = (h: Harnesses | null, harness: string | null | undefined) => h?.profiles.find((p) => p.id === harness)?.provider ?? harness ?? "";
const pairing = (t: MTask, h: Harnesses | null) => {
  const prof = t.profile ? h?.agent_profiles.find((p) => p.id === t.profile) : undefined;
  const provider = providerOf(h, t.harness);
  return prof ? { model: prof.providers?.[provider]?.model ?? prof.model?.[provider] ?? null, effort: prof.providers?.[provider]?.effort ?? null } : null;
};

/** The model an agent task launches on, the way `resolve_agent_task` picks it
 *  below this item's own overrides: its agent profile's for its harness's
 *  provider, else its own, else the repo's `models:` for its harness, else the
 *  harness's default. */
export function modelOf(t: MTask, h: Harnesses | null, repo?: Pick<Repo, "models"> | null): Given {
  const p = pairing(t, h);
  if (p?.model) return { value: `${p.model} · ${t.profile}`, source: "profile" };
  if (t.profile) return { value: `${t.profile} profile`, source: "chain" };
  if (t.model) return { value: t.model, source: "chain" };
  const r = t.harness ? repo?.models?.[t.harness] : undefined;
  if (r) return { value: r, source: "repo" };
  const d = h?.profiles.find((x) => x.id === t.harness)?.defaults?.model;
  return d ? { value: d, source: "harness" } : { value: `${t.harness ?? "harness"} default`, source: "harness" };
}

/** The effort an agent task runs at, the same way: its profile's for the provider, else its own, else the harness's default. */
export function effortOf(t: MTask, h: Harnesses | null): Given {
  const p = pairing(t, h);
  if (p?.effort) return { value: `${p.effort} · ${t.profile}`, source: "profile" };
  if (!t.profile && t.effort) return { value: t.effort, source: "chain" };
  const d = h?.profiles.find((x) => x.id === t.harness)?.defaults?.effort;
  return d ? { value: d, source: "harness" } : { value: `${t.harness ?? "harness"} default`, source: "harness" };
}

/** One value for many tasks: theirs when they agree, else "each task's own" from the chain. */
export const oneOf = (values: Given[]): Given =>
  values.length && values.every((v) => v.value === values[0].value && v.source === values[0].source) ? values[0] : { value: "each task's own", source: "chain" };

/** The providers the chain's agent tasks (or one node's) run on. */
export const providersOf = (m: Materialized, h: Harnesses | null, node?: string) => [...new Set(agentTasks(m, node).map((t) => providerOf(h, t.harness)))];
export { providerOf };

/** The models to suggest for tasks on `providers`: what each provider lists,
 *  each agent profile's model for it and each harness's default on it. */
export function modelSuggestions(providers: string[], listed: { id: string; models: string[] }[], h: Harnesses | null): string[] {
  const out = providers.flatMap((p) => [
    ...(listed.find((x) => x.id === p)?.models ?? []),
    ...(h?.agent_profiles.flatMap((a) => (a.model?.[p] ? [a.model[p]] : [])) ?? []),
    ...(h?.profiles.flatMap((x) => (x.provider === p && x.defaults?.model ? [x.defaults.model] : [])) ?? []),
  ]);
  return [...new Set(out)];
}

/** The efforts every one of `providers` accepts; empty when none says. */
export function effortOptions(providers: string[], listed: { id: string; efforts: string[] }[]): string[] {
  const sets = providers.map((p) => listed.find((x) => x.id === p)?.efforts ?? []).filter((e) => e.length);
  return sets.length ? sets.reduce((a, b) => a.filter((x) => b.includes(x))) : [];
}

/** A cap as a row shows it: minutes, dollars to the cent, or a count. */
export function capText(key: string, v: number | null): string {
  if (v == null) return "no cap";
  if (key === "budget_usd") return `$${v.toFixed(2)}`;
  return key.endsWith("_minutes") ? `${v}m` : String(v);
}
