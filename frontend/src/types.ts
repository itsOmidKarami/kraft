export interface ChainNode {
  id: string;
  tasks: string[];
  gate_after: string | null;
  fix_loop?: string;
}

export interface ChainDefinition {
  template_id: string;
  nodes: ChainNode[];
}

export type WorkItemStatus = "active" | "needs_human" | "completed";

export interface WorkItem {
  id: string;
  title: string;
  repo: string;
  status: WorkItemStatus;
  chain_template: string;
  chain_definition: ChainDefinition;
  current_node_id: string | null;
  bead_id: string | null;
  created_at: string;
  updated_at: string;
  // client-derived, not from the list endpoint:
  pendingGate?: string | null;
  rejectNote?: string | null;
  fixCycle?: number;
  completedNodes?: string[];
}

export type SessionStatus =
  | "pending"
  | "running"
  | "done"
  | "failed"
  | "capped_out"
  | "paused"
  | "unknown";

export interface WorkerSession {
  id: string;
  work_item_id: string;
  node_id: string;
  hook_point: string;
  status: SessionStatus;
  attempt: number;
  created_at: string;
  exited_at: string | null;
}

export interface KraftEvent {
  seq: number;
  work_item_id: string;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface Health {
  status: "ok" | "degraded";
  invalid_templates: Record<string, string>;
  invalid_policy: string[];
}
