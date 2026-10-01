import type { ChainNode } from "../../../types";
import type { DraftView, MarkedOp } from "./types";

/** Test data, not a test file: the four-node chain of `testkit` as a draft answer. */
export const chain = (...ids: string[]): ChainNode[] => ids.map((id) => ({ id, kind: "exec", gate_after: null, tasks: [`${id}.main.run`], steps: [[`${id}.main.run`]] }));
export const NODES = chain("plan", "build", "verify", "ship");
export const view = (ops: MarkedOp[] = [], over: Partial<DraftView> = {}): DraftView => ({ ops, problems: [], checks: { budget: { spent_usd: 1, cap_usd: 10 } }, nodes: NODES, base_seq: ops.length ? 1 : null, updated_at: null, ...over });
export const item = (current: string | null = "build", status = "active") => ({ id: "w1", status, current_node_id: current });
