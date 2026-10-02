import type { Harnesses } from "../../types";
import type { ItemDetail } from "./useItem";

/** The item's own chain as intake froze it (`materialized_chain`): what a
 *  not-yet-started item's Config rows read the chain's values from. Only the
 *  parts those rows show are typed. */
type Caps = Record<string, number | null | undefined>;
type Pol = Record<string, unknown> | null | undefined;
export type MTask = { id: string; kind: string; harness?: string | null; model?: string | null; effort?: string | null; profile?: string | null; prompt?: string | null; command?: unknown; policy?: Pol };
type MStep = { id: string; tasks: MTask[]; policy?: Pol };
export type MNode = { id: string; kind: string; tasks?: MTask[] | null; steps?: MStep[] | null; policy?: Pol; fix_loop?: { max_attempts?: number | null } | null };
export type Materialized = {
  chain: { nodes: MNode[]; policy?: Pol };
  policy?: Record<string, unknown> & { cap_defaults?: Record<string, Caps>; maxima?: Record<string, unknown> };
};

const ENDED = new Set(["done", "cancelled", "archived"]);

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

/** The node, step and task a path names, broadest first. */
function scopes(m: Materialized, path: string) {
  const [n, s, t] = path.split(".");
  const node = m.chain.nodes.find((x) => x.id === n);
  const step = node && s ? stepsOfNode(node).find((x) => x.id === s) : undefined;
  const task = step && t ? step.tasks.find((x) => x.id === t) : undefined;
  return { node, step, task };
}

export const taskAt = (m: Materialized, path: string) => scopes(m, path).task;
export const nodeAt = (m: Materialized, id: string) => m.chain.nodes.find((n) => n.id === id);
/** The agent tasks under a node, or under the whole chain. */
export const agentTasks = (m: Materialized, node?: string): MTask[] =>
  m.chain.nodes.filter((n) => node === undefined || n.id === node).flatMap((n) => stepsOfNode(n).flatMap((s) => s.tasks)).filter((t) => t.kind === "agent");

const LEVELS = ["work_item", "nodes", "steps", "tasks"];
const num = (v: unknown) => (typeof v === "number" ? v : null);

/** A cap (`time_cap_minutes`, `total_time_cap_minutes`, `budget_usd`,
 *  `token_budget`) as the scope at `path` runs under it from the chain alone:
 *  the narrowest layer that sets it, else its level's default, held to the
 *  nearest maximum (the server's `policy_for` and `at_level`, without this
 *  item's own overrides). Null: no cap. */
export function capAt(m: Materialized, path: string, key: string): number | null {
  const { node, step, task } = scopes(m, path);
  const level = task ? "tasks" : step ? "steps" : "nodes";
  const layers = [m.policy, m.chain.policy, node?.policy, step?.policy, task?.policy];
  let value = layers.reduce<number | null>((v, l) => num(l?.[key]) ?? v, null);
  value ??= num(m.policy?.cap_defaults?.[level]?.[key]);
  for (const at of LEVELS.slice(0, LEVELS.indexOf(level) + 1).reverse()) {
    const bound = num((m.policy?.maxima?.[at] as Caps | undefined)?.[key]);
    if (bound != null) return value == null ? bound : Math.min(value, bound);
  }
  return value;
}

const providerOf = (h: Harnesses | null, harness: string | null | undefined) => h?.profiles.find((p) => p.id === harness)?.provider ?? harness ?? "";

/** The model an agent task runs on from the chain: its own, else its agent
 *  profile's for its harness's provider, else the harness's default. */
export function modelOf(t: MTask, h: Harnesses | null): string {
  if (t.model) return t.model;
  const prof = t.profile ? h?.agent_profiles.find((p) => p.id === t.profile) : undefined;
  if (t.profile) {
    const m = prof?.model?.[providerOf(h, t.harness)];
    return m ? `${m} · ${t.profile} profile` : `${t.profile} profile`;
  }
  const d = h?.profiles.find((p) => p.id === t.harness)?.defaults?.model;
  return d ? `${d} · ${t.harness} default` : `${t.harness ?? "harness"} default`;
}

/** The effort an agent task runs at from the chain, the same way. */
export function effortOf(t: MTask, h: Harnesses | null): string {
  if (t.effort) return t.effort;
  const prof = t.profile ? h?.agent_profiles.find((p) => p.id === t.profile) : undefined;
  if (prof?.effort) return `${prof.effort} · ${t.profile} profile`;
  const d = h?.profiles.find((p) => p.id === t.harness)?.defaults?.effort;
  return d ? `${d} · ${t.harness} default` : `${t.harness ?? "harness"} default`;
}

/** One value for many tasks: theirs when they agree, else "each task's own". */
export const oneOf = (values: string[]) => (values.length && values.every((v) => v === values[0]) ? values[0] : "each task's own");

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
