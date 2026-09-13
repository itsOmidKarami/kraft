import type { ChainNode } from "./types/work_item";

/** Mirrors `templates.NODE_CARRYOVER_FIELDS` (`src/kraft/templates.py`) --
 *  the node keys the chain-review skill's schema never required an agent to
 *  reproduce, carried forward from the node an "unchanged" revision
 *  replaces. Same order as the Python list on purpose. */
const NODE_CARRYOVER_FIELDS = [
  "on_failure",
  "reject_to",
  "rebase_bounce_to",
  "auto_escalate",
  "auto_escalate_stuck",
  "auto_escalate_delay_s",
] as const;

/** Mirrors `templates.NON_PROPOSABLE_CARRYOVER_FIELDS` -- the
 *  `NODE_CARRYOVER_FIELDS` a chain-review node is never allowed to set for
 *  itself; `auto_escalate`/`auto_escalate_stuck`/`auto_escalate_delay_s` are
 *  the human PATCH route's alone. */
const NON_PROPOSABLE_CARRYOVER_FIELDS = [
  "auto_escalate",
  "auto_escalate_stuck",
  "auto_escalate_delay_s",
] as const;

/** `templates.strip_non_proposable_carryover_fields`, in TypeScript -- must
 *  run before `carryForwardNodeFields` so an agent-authored value can never
 *  reach the preview; only the carry-forward path may set these. */
function stripNonProposableCarryoverFields(
  nodes: Record<string, unknown>[],
): Record<string, unknown>[] {
  for (const n of nodes) {
    for (const field of NON_PROPOSABLE_CARRYOVER_FIELDS) {
      delete n[field];
    }
  }
  return nodes;
}

/** `templates.carry_forward_node_fields`, in TypeScript -- the gate's
 *  preview must show the tail exactly as `_splice_chain_review` will apply
 *  it, not the reviewer's raw, schema-limited output (Kraft-df4tc). */
export function carryForwardNodeFields(
  oldNodes: ChainNode[],
  newNodes: Record<string, unknown>[],
): Record<string, unknown>[] {
  const oldById = new Map(oldNodes.map((n) => [n.id, n]));
  return stripNonProposableCarryoverFields(newNodes).map((n) => {
    const old = oldById.get(n.id as string);
    const filled = { ...n };
    for (const field of NODE_CARRYOVER_FIELDS) {
      if (!(field in filled)) {
        filled[field] = old ? ((old as unknown as Record<string, unknown>)[field] ?? null) : null;
      }
    }
    return filled;
  });
}

/** One flagged permission-surface concern (design point 3) -- read-only,
 *  never a proposed value. */
export type ChainReviewFlag = {
  hook_point: string;
  field: string;
  current_value: unknown;
  concern: string;
};

export type ChainReviewEnvelope = {
  status: "ready_for_approval" | "error";
  revised_chain_nodes?: Record<string, unknown>[];
  rationale?: string;
  flags?: ChainReviewFlag[];
};

/** The chain-review artifact's JSON envelope, after its mandatory front
 *  matter block -- the TS side of `gates._strip_front_matter` +
 *  `json.loads`. `null` on anything that isn't the envelope shape, so a
 *  caller can fall back to plain Markdown rather than crash on a hand-
 *  edited or malformed artifact. */
export function parseChainReviewArtifact(content: string): ChainReviewEnvelope | null {
  const body = content.startsWith("---\n")
    ? content.slice(content.indexOf("\n---\n", 4) + 5)
    : content;
  try {
    const parsed: unknown = JSON.parse(body);
    if (
      parsed &&
      typeof parsed === "object" &&
      "status" in parsed &&
      ("revised_chain_nodes" in parsed ? Array.isArray((parsed as { revised_chain_nodes?: unknown }).revised_chain_nodes) : true)
    ) {
      return parsed as ChainReviewEnvelope;
    }
  } catch {
    /* not JSON -- not a chain_review artifact, or malformed */
  }
  return null;
}
