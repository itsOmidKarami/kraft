import type { ChainNode, KraftEvent, WorkerSession, WorkItem } from "../../../types";

/** Shared `WorkItem`/`WorkerSession`/`KraftEvent` factories for the
 *  ActionBar test suite — not a `.test.*` file itself so importing it
 *  doesn't re-run another file's `describe` blocks. */

const NODES: ChainNode[] = [
  { id: "spec", tasks: ["on.spec.requested"], gate_after: "spec_approval" },
  { id: "plan", tasks: ["on.plan.requested"], gate_after: "plan_approval" },
  { id: "verify", tasks: ["on.test.run"], gate_after: null },
];

export const item = (over: Partial<WorkItem> = {}): WorkItem =>
  ({
    id: "w1",
    title: "T",
    repo: "/r",
    status: "active",
    chain_template: "default",
    chain_definition: { template_id: "default", nodes: NODES },
    current_node_id: "verify",
    bead_id: "B",
    created_at: "t",
    updated_at: "t",
    ...over,
  }) as WorkItem;

export const session = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "s1",
    work_item_id: "w1",
    node_id: "verify",
    hook_point: "on.test.run",
    status: "running",
    attempt: 1,
    thread: 1,
    round: 0,
    created_at: "t",
    started_at: "t",
    exited_at: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    wall_ms: null,
    model: null,
    head_sha: null,
    ...over,
  }) as WorkerSession;

export const escSession = (over: Partial<WorkerSession> = {}): WorkerSession =>
  ({
    id: "e1",
    work_item_id: "w1",
    node_id: "verify",
    hook_point: "escalation",
    status: "running",
    attempt: 1,
    thread: 1,
    round: 0,
    created_at: "2026-01-01T00:05:00Z",
    started_at: "2026-01-01T00:05:00Z",
    exited_at: null,
    tokens_in: null,
    tokens_out: null,
    cost_usd: null,
    wall_ms: null,
    model: null,
    head_sha: null,
    ...over,
  }) as WorkerSession;

export const NEEDS_HUMAN_EVENT: KraftEvent = {
  seq: 1,
  work_item_id: "w1",
  type: "work_item_needs_human",
  payload: { reason: "loop capped" },
  created_at: "2026-01-01T00:00:00Z",
};

/** The `escalation_message` event that ties an `escSession()` to its
 *  episode (Kraft-bffrk) — deriveState scopes a turn by this, not by
 *  comparing `created_at` against the boundary event. */
export const escMessage = (over: Partial<KraftEvent> = {}): KraftEvent =>
  ({
    seq: 2,
    work_item_id: "w1",
    type: "escalation_message",
    payload: { session_id: "e1", message: "go" },
    created_at: "2026-01-01T00:05:00Z",
    ...over,
  }) as KraftEvent;
