import type { ChainNode } from "../../../types";

/** The item draft's four ops (docsite "Item drafts"). */
export type OverrideOp = { op: "override"; path: string; task_config?: Record<string, unknown>; policy?: Record<string, unknown> };
export type AddNodeOp = { op: "add_node"; after: string; node: { id: string; extends?: string } & Record<string, unknown> };
export type RemoveNodeOp = { op: "remove_node"; node: string };
export type SkipOp = { op: "skip"; path: string };
export type Op = OverrideOp | AddNodeOp | RemoveNodeOp | SkipOp;
/** An op as the server answers it: `passed` once the run has gone by where it acts. */
export type MarkedOp = Op & { passed: boolean };

export type Problem = { op: number; message: string };
export type Budget = { spent_usd: number | null; cap_usd: number | null };

/** `GET|PUT /work-items/{id}/draft`, and apply's 200. */
export type DraftView = {
  ops: MarkedOp[];
  problems: Problem[];
  checks: { budget: Budget };
  /** The chain with every op applied that is neither passed nor a problem. */
  nodes: ChainNode[];
  base_seq: number | null;
  updated_at: string | null;
};

/** A refusal's body: a 409's `passed` indexes, a 422's `problems`, always a `detail`. */
export type Refusal = { detail?: unknown; passed?: number[]; problems?: Problem[] };
