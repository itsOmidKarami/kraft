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

export type WorkItemStatus =
  | "active"
  | "needs_human"
  | "completed"
  | "paused"
  // Terminal, and off the board unless explicitly asked for (Kraft-x85).
  | "abandoned";

export interface WorkItemAttachment {
  kind: "spec" | "plan";
  /** Repo-relative path, normalized by the server. */
  path: string;
}

export interface WorkItem {
  id: string;
  title: string;
  /** The brief, in prose. Prepended to every agent's task instruction; the
   *  title alone is only a label. */
  description?: string | null;
  repo: string;
  status: WorkItemStatus;
  chain_template: string;
  chain_definition: ChainDefinition;
  current_node_id: string | null;
  bead_id: string | null;
  /** Steer text left while paused; consumed by the next agent launch. */
  pending_steer_context?: string | null;
  created_at: string;
  updated_at: string;
  /** The gate waiting on a person, straight from the server — a rejected gate
   *  is not pending, which no client-side inference from sessions can see. */
  pending_gate?: string | null;
  /** Repo-relative path to the document the pending gate is a decision about,
   *  or null when the agent wrote nothing for a human to review. */
  gate_artifact?: string | null;
  // client-derived, not from the list endpoint:
  rejectNote?: string | null;
  fixCycle?: number;
  completedNodes?: string[];
  /** Set when a loop cap is what stopped the item — the board shows "capped n/n". */
  cappedOut?: { cycles: number; attempts: number } | null;
  /** Set when a spend cap is what stopped the item (sub-project E §3). */
  budget?: { scope: "work_item" | "daily"; spent_usd: number; cap_usd: number } | null;
  /** Only on the detail endpoint, not the list. */
  usage?: WorkItemUsage;
  /** Empty on a single-repo item; ordered deepest submodule first. */
  repos?: RepoRow[];
  /** Documents attached at intake; the gates they satisfy are absent from the chain. */
  attachments?: WorkItemAttachment[];
  root_merge_policy?: string | null;
  worktree_path?: string;
  /** why the item is stopped, from the `work_item_needs_human` it sits on */
  stop_reason?: string | null;
  /** Minor findings that never entered the fix loop; only on the detail endpoint. */
  deferred_findings?: Finding[];
  /** `done_with_concerns` text from every session that reported one; only on the detail endpoint. */
  concerns?: string[];
  /** The agent's question, set only while a `needs_human` stop is answerable
   *  as a `needs_context` one; only on the detail endpoint. */
  needs_context_question?: string | null;
}

export interface Finding {
  severity: string;
  message: string;
  file: string | null;
  line: number | null;
  source_plugin: string;
}

export type SessionStatus =
  | "pending"
  | "running"
  | "done"
  | "done_with_concerns"
  | "needs_context"
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
  /** Fix-cycle index this session was dispatched in; 0 on the first pass. */
  round: number;
  created_at: string;
  started_at: string | null;
  exited_at: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  wall_ms: number | null;
  model: string | null;
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
  /** Where the server is listening — the login screen tells the user. */
  bind?: string;
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
  /** Set when this row is an intake attachment rather than an agent-written link. */
  attachment_kind: "spec" | "plan" | null;
}

export interface WorkItemDiff {
  work_item_id: string;
  base_ref: string | null;
  files: { path: string; insertions: number; deletions: number }[];
  diff: string;
  untracked: string[];
  truncated: boolean;
}

/** The document a gate is a decision about, read off the worktree. */
export interface WorkItemArtifact {
  work_item_id: string;
  path: string;
  title: string;
  content: string;
  truncated: boolean;
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

export interface UsageRollup {
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  /** False when a session spent tokens but reported no cost — the sum is a floor. */
  cost_complete: boolean;
  wall_ms: number;
  sessions: number;
  rounds: number;
  capped_out: number;
}

export interface WorkItemUsage {
  total: UsageRollup;
  by_node: (UsageRollup & { node: string })[];
}

/** One line of a worker session's log (GET /worker-sessions/{id}/log?format=jsonl). */
export interface LogLine {
  n: number;
  t: string | null;
  src: "sys" | "stdout" | "agent" | "tool";
  text: string;
}

export interface Analytics {
  totals: {
    work_items: number;
    by_status: Record<string, number>;
    mrs_merged: number;
    wall_ms: number;
    human_wait_ms: number;
    tokens_in: number;
    tokens_out: number;
    cost_usd: number;
    cost_complete: boolean;
    rounds: number;
    capped_out: number;
  };
  weekly_merged: { week_start: string; n: number }[];
  by_node: {
    node: string;
    runs: number;
    wall_ms: number;
    avg_ms: number;
    tokens: number;
    cost_usd: number;
    cost_complete: boolean;
    rounds: number;
    capped_out: number;
  }[];
  by_repo: {
    repo: string;
    items: number;
    mrs: number;
    tokens: number;
    cost_usd: number;
    cost_complete: boolean;
  }[];
}

/* ── settings (design 5a–5e) ─────────────────────────────────────────────── */

export interface Repo {
  path: string;
  name: string;
  default_chain_template: string;
  test_command: string | null;
  forge: string | null;
  project: string | null;
  enabled: boolean;
}

export interface RepoProbe {
  path: string;
  name: string;
  branch: string | null;
  submodules: string[];
  has_beads: boolean;
  beads_export_auto: boolean;
  beads_export_git_add: boolean;
  has_engineering: boolean;
  test_command: string | null;
  forge: string | null;
  project: string | null;
}

export interface TemplateNode {
  id: string;
  tasks: string[];
  gate_after: string | null;
  fix_loop?: string | null;
}

export interface TemplateSummary {
  id: string;
  nodes: TemplateNode[];
  gates: number;
}

export interface TemplateValidation {
  id: string;
  valid: boolean;
  error: string | null;
  by_repo: { repo: string; resolvable: boolean }[];
}

export interface HookBinding {
  kind: "builtin" | "agent" | "subprocess";
  handler?: string;
  command?: string | string[];
  interactive?: boolean;
  repos?: Record<string, boolean>;
}

export interface SteeringFile {
  name: string;
  bytes: number | null;
}

export interface SteeringList {
  files: SteeringFile[];
  max_bytes: number;
}

export interface Intake {
  enabled: boolean;
  interval_s: number;
  max_concurrent: number;
  priority_ceiling: number;
  repos: string[];
}

export interface Cap {
  attempts: number;
  wall_clock_s: number;
}

export interface Policy {
  loops: Record<string, Cap>;
  default: Cap;
  findings?: { loop_severities?: string[] };
  budget?: { work_item_usd: number | null; daily_usd: number | null };
}

export interface Access {
  bind: string;
  port: number;
  session_expiry_days: number;
  password_set: boolean;
  auth_required: boolean;
}

/** `GET /notify`. The webhook URL is deliberately absent — the server never
 *  sends it back, so there is nothing here to accidentally render. */
export interface Notify {
  enabled: boolean;
  url_set: boolean;
  base_url: string | null;
  events: string[];
}

export interface AuthSession {
  id: string;
  label: string | null;
  ip: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
}

export interface Bead {
  id: string;
  title: string;
  status: string | null;
  issue_type: string | null;
}

export interface RepoRow {
  repo: string;
  path: string;
  role: "root" | "submodule";
  merge_rank: number;
  state: string;
}
