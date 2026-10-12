import type { FixTarget } from "../../../frontend/src/types";

/** A node Request changes may restart at; the server's own choice comes first. */
export type RestartChoice = { node: string; isDefault: boolean; label: string; description?: string };

export const NO_ROUNDS = "No fix rounds left.";

export const outOfRounds = (fix: FixTarget) => !!fix.round && fix.round.n > fix.round.max;

/**
 * Where Request changes may restart: at a gate, any non-gate node before it
 * (`executor.reject_target`); with none, any non-gate node at or before the
 * current one (`_request_changes_now`). The server still judges the pick.
 */
export function restartChoices(
  nodes: readonly { id: string; kind?: string }[],
  at: { gate: string } | { current: string },
  fix: FixTarget,
): RestartChoice[] | { refused: string } {
  if (outOfRounds(fix)) return { refused: NO_ROUNDS };
  const last = "gate" in at ? nodes.findIndex((n) => n.id === at.gate) - 1 : nodes.findIndex((n) => n.id === at.current);
  const valid = nodes.filter((n, i) => i <= last && n.kind !== "gate").map((n) => n.id);
  const description = "gate" in at ? `then ${[...fix.then, at.gate].join(" → ")}` : fix.reason ? `why: ${fix.reason}` : undefined;
  return [
    // First even when the view does not list it: the server knows something the view does not.
    { node: fix.node, isDefault: true, label: `${fix.node} (default)`, ...(description && { description }) },
    ...valid.filter((id) => id !== fix.node).map((id) => ({ node: id, isDefault: false, label: id })),
  ];
}

/** The default goes unnamed: the server picks the same node. */
export const nodeToSend = (c: RestartChoice): string | undefined => (c.isDefault ? undefined : c.node);
