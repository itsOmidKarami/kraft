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

export interface DocumentLink {
  work_item_id: string | null;
  node_id: string | null;
  hook_point: string | null;
  worker_session_id: string | null;
}

export interface WorkItemDocument {
  document_id: string;
  repo: string;
  title: string;
  kind: string | null;
  source_kind: string;
  path: string;
  node_id: string | null;
  hook_point: string | null;
  worker_session_id: string | null;
}

export interface SearchResult {
  id: string;
  repo: string;
  source_kind: "artifact" | "session_summary";
  kind: string | null;
  title: string;
  path: string;
  snippet: string;
  score: number;
  links: DocumentLink[];
}

export interface SearchResponse {
  query: string;
  mode: string;
  results: SearchResult[];
}

export interface DocumentDetail {
  id: string;
  repo: string;
  source_kind: "artifact" | "session_summary";
  kind: string | null;
  title: string;
  path: string;
  content: string;
  metadata: Record<string, unknown>;
  source_created_at: string | null;
  source_updated_at: string | null;
  indexed_at: string;
  links: DocumentLink[];
}
