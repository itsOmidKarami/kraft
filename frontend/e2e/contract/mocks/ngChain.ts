import shipped from "./shippedChains.json" with { type: "json" };

/**
 * The chains the demo's work items run: the shipped ones, as a real server freezes them for an item filed paused
 * (`shippedChains.json`, which `just shapes` rewrites from `tests/api/test_demo_chain.py`), and `docs_only`, a chain a
 * person wrote from five of the default chain's nodes. Each has the API's `chain_definition` nodes and the frozen
 * chain (the `chain` of the item's `materialized_chain`), from which taskKinds.ts reads what each task is.
 */

/** A `chain_definition` node, as the API sends it: tasks as hook-point paths, steps as arrays of them. */
export type DefNode = (typeof shipped.chains.default.chain_definition.nodes)[number] & Record<string, any>;
export type Chain = { id: string; nodes: DefNode[]; frozen: any };

const clone = <T,>(x: T): T => JSON.parse(JSON.stringify(x));
const recorded = (id: "default" | "quick-task"): Chain => ({ id, nodes: clone(shipped.chains[id].chain_definition.nodes) as DefNode[], frozen: clone(shipped.chains[id].chain) });
/** `chain`'s nodes named in `ids`, in its order, in both its definition and its frozen chain. */
const only = (chain: Chain, id: string, ids: string[]): Chain => ({
  id, nodes: chain.nodes.filter((n) => ids.includes(n.id)), frozen: { ...chain.frozen, id, nodes: chain.frozen.nodes.filter((n: any) => ids.includes(n.id)) },
});

const DEFAULT = recorded("default");
export const CHAINS: Record<string, Chain> = {
  default: DEFAULT,
  "quick-task": recorded("quick-task"),
  docs_only: only(DEFAULT, "docs_only", ["spec", "spec_approval", "implementation", "work_brief", "draft_merge_request"]),
};

/** The chain a template names, or undefined for one the mock does not have. A copy: an item changes its own. */
export const chainOf = (template: string): Chain | undefined => (CHAINS[template] ? clone(CHAINS[template]) : undefined);

/** An item's `materialized_chain` for `template`, kept to `keep` node ids when given (a chain the dry run trimmed). */
export function frozenOf(template: string, keep?: string[]): string {
  const c = CHAINS[template];
  return JSON.stringify({ chain: { ...c.frozen, nodes: c.frozen.nodes.filter((n: any) => !keep || keep.includes(n.id)) } });
}
