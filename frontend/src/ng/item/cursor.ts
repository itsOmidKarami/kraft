import type { ChainNode } from "../../types";

/** Whether `current` (a current node id or a stop's node) stands on node `id`: the node itself, or a path inside it
 *  (`slices.implementation` and `slices.implementation.build.code` are in `slices`). */
export const within = (id: string, current: string | null | undefined) => !!current && (current === id || current.startsWith(`${id}.`));

/** Where a current node id stands in a chain: the top-level node and its index, and, when the run stands inside a
 *  group (`group.node[.step...]`), the body node it is on and that node's index in the body. A body node's id is its
 *  path (`slices.implementation`, as the server's `node_view` sends it). Null when the id is null or names no node. */
export type Cursor = { index: number; node: ChainNode; inner: ChainNode | null; innerIndex: number };

export function cursorOf(nodes: ChainNode[], current: string | null | undefined): Cursor | null {
  if (!current) return null;
  const index = nodes.findIndex((n) => within(n.id, current));
  if (index < 0) return null;
  const node = nodes[index];
  const body = node.kind === "group" ? (node.nodes ?? []) : [];
  const innerIndex = body.findIndex((b) => within(b.id, current));
  return { index, node, inner: innerIndex < 0 ? null : body[innerIndex], innerIndex };
}

/** The node a current id names, the body node itself inside a group: what a retry's path and a node's name start from. */
export const standingNode = (nodes: ChainNode[], current: string | null | undefined): ChainNode | undefined => {
  const c = cursorOf(nodes, current);
  return c ? (c.inner ?? c.node) : undefined;
};

/** The chain node `id` names: a top-level node, or one inside a group by its path (`slices.implementation`). */
export function findNode(nodes: ChainNode[], id: string | null | undefined): ChainNode | undefined {
  if (!id) return undefined;
  return nodes.find((n) => n.id === id) ?? nodes.flatMap((n) => (n.kind === "group" ? (n.nodes ?? []) : [])).find((b) => b.id === id);
}

/** The node after `id`: the next one in its group's body, else the next in the chain (after a group, its last node too). */
export function nextNode(nodes: ChainNode[], id: string): ChainNode | undefined {
  const c = cursorOf(nodes, id);
  if (!c) return undefined;
  const body = c.node.kind === "group" ? (c.node.nodes ?? []) : [];
  return (c.inner && body[c.innerIndex + 1]) || nodes[c.index + 1];
}
