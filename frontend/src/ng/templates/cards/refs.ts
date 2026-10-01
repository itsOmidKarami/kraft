import type { Result } from "../draft/types";
import { authoredNodes } from "../draft/view";

export type Ref = { node: string; path: string };

/** What points at a node in this chain's file: a gate's `reject_to` and a
 *  node's `on_base_changed.restart_from` (W9 rename rewrites exactly these). */
export function refsTo(r: Result, chain: string, id: string): Ref[] {
  const out: Ref[] = [];
  for (const n of authoredNodes(r, chain)) {
    if (n.id === id) continue;
    if (n.reject_to === id) out.push({ node: n.id, path: `${n.id}.reject_to` });
    const obc = n.on_base_changed as { restart_from?: unknown } | null | undefined;
    if (obc?.restart_from === id) out.push({ node: n.id, path: `${n.id}.on_base_changed.restart_from` });
  }
  return out;
}
